"""Confirmation tokens for draft actions.

A token is:
- generated server-side (secrets.token_urlsafe, 256 bits),
- shown only to the browser of the requesting user (never to the model, never stored in clear),
- bound to user + action + canonical parameters: token_hash = sha256(token | params_hash),
  where params_hash = sha256(user | action | canonical_json(params)),
- short-lived (expires_at) and single-use (atomic Pending -> Executing transition).
"""

import hashlib
import hmac
import json
import secrets

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from frappe_ai_assistant.frappe_ai_assistant import ACTION_REQUEST
from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit


class ConfirmationError(frappe.ValidationError):
	pass


def canonical(params: dict) -> str:
	return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def params_hash(user: str, action: str, params: dict) -> str:
	return hashlib.sha256(f"{user}|{action}|{canonical(params)}".encode()).hexdigest()


def token_hash(token: str, p_hash: str) -> str:
	return hashlib.sha256(f"{token}|{p_hash}".encode()).hexdigest()


def create_action_request(*, action: str, params: dict, preview: dict, summary: str, target_doctype: str,
						  conversation: str | None, message: str | None, expiry_minutes: int = 10):
	"""Returns (action_request_name, token). The token must only go to the user's browser."""
	user = frappe.session.user
	token = secrets.token_urlsafe(32)
	p_hash = params_hash(user, action, params)
	doc = frappe.get_doc({
		"doctype": ACTION_REQUEST,
		"action": action,
		"status": "Pending",
		"user": user,
		"conversation": conversation,
		"message": message,
		"target_doctype": target_doctype,
		"expires_at": add_to_date(now_datetime(), minutes=max(1, int(expiry_minutes or 10))),
		"summary": summary[:500],
		"params_json": canonical(params),
		"preview_json": json.dumps(preview, default=str, ensure_ascii=False),
		"params_hash": p_hash,
		"token_hash": token_hash(token, p_hash),
	})
	doc.insert(ignore_permissions=True)  # read-only DocType; written only through this module
	return doc.name, token


def reissue_token(name: str) -> str:
	"""New token for a still-pending request (e.g. after a page reload). Owner only."""
	row = _load_owned(name)
	if row.status != "Pending" or row.expires_at <= now_datetime():
		frappe.throw(_("This action can no longer be confirmed."), ConfirmationError)
	token = secrets.token_urlsafe(32)
	frappe.db.set_value(ACTION_REQUEST, name, "token_hash", token_hash(token, row.params_hash),
						update_modified=False)
	return token


def _load_owned(name: str):
	row = frappe.db.get_value(
		ACTION_REQUEST, name,
		["name", "user", "action", "status", "expires_at", "params_json", "params_hash", "token_hash",
		 "conversation"],
		as_dict=True,
	)
	if not row or row.user != frappe.session.user:
		if row:
			audit("action_owner_mismatch", "High", details={"action_request": name})
		frappe.throw(_("Action not found."), frappe.DoesNotExistError)
	return row


def claim(name: str, token: str):
	"""Validate token and atomically claim the request for execution. Returns (row, params)."""
	row = _load_owned(name)
	if not isinstance(token, str) or len(token) < 20:
		audit("token_invalid", "High", conversation=row.conversation, details={"action_request": name})
		frappe.throw(_("Invalid confirmation."), ConfirmationError)

	params = json.loads(row.params_json or "{}")
	recomputed = params_hash(row.user, row.action, params)
	if not hmac.compare_digest(recomputed, row.params_hash or ""):
		audit("param_tamper", "Critical", conversation=row.conversation, details={"action_request": name})
		frappe.throw(_("This action was changed after it was prepared and can't be confirmed."), ConfirmationError)

	if not hmac.compare_digest(token_hash(token, recomputed), row.token_hash or ""):
		audit("token_invalid", "High", conversation=row.conversation, details={"action_request": name})
		frappe.throw(_("Invalid confirmation."), ConfirmationError)

	if row.status != "Pending":
		audit("token_replay", "High", conversation=row.conversation,
			  details={"action_request": name, "status": row.status})
		frappe.throw(_("This action was already handled."), ConfirmationError)

	if row.expires_at <= now_datetime():
		frappe.db.set_value(ACTION_REQUEST, name, "status", "Expired", update_modified=False)
		audit("token_expired", "Warning", conversation=row.conversation, details={"action_request": name})
		frappe.throw(_("This confirmation has expired. Please ask Chikku AI to prepare it again."),
					 ConfirmationError)

	# Atomic single-use claim: only one request can move Pending -> Executing.
	frappe.db.sql(
		f"""update `tab{ACTION_REQUEST}` set status='Executing', confirmed_at=%s
		where name=%s and status='Pending' and expires_at > %s""",
		(now_datetime(), name, now_datetime()),
	)
	claimed = frappe.db.sql("select row_count()")[0][0]
	if claimed != 1:
		audit("token_replay", "High", conversation=row.conversation,
			  details={"action_request": name, "race": True})
		frappe.throw(_("This action was already handled."), ConfirmationError)
	return row, params


def finish(name: str, *, status: str, result_doctype=None, result_name=None, error=None):
	frappe.db.set_value(
		ACTION_REQUEST, name,
		{"status": status, "executed_at": now_datetime(), "result_doctype": result_doctype,
		 "result_name": result_name, "error": (error or "")[:1000] or None},
		update_modified=False,
	)


def cancel(name: str):
	row = _load_owned(name)
	if row.status == "Pending":
		frappe.db.set_value(ACTION_REQUEST, name, "status", "Cancelled", update_modified=False)
	return row


def expire_stale():
	frappe.db.sql(
		f"update `tab{ACTION_REQUEST}` set status='Expired' where status='Pending' and expires_at <= %s",
		(now_datetime(),),
	)
