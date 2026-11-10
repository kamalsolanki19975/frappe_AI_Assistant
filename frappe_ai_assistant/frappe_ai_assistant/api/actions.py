"""Confirmation endpoints for draft actions. Only a real user click (session + CSRF + token) reaches here.

Errors are returned as {"ok": False, ...} instead of raised, so status changes and audit rows
written during validation are committed rather than rolled back with the request."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr, get_url_to_form

from frappe_ai_assistant.frappe_ai_assistant import ACTION_REQUEST, REALTIME_EVENT
from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit
from frappe_ai_assistant.frappe_ai_assistant.security import limits, tokens
from frappe_ai_assistant.frappe_ai_assistant.security.access import capability_allowed, require_access
from frappe_ai_assistant.frappe_ai_assistant.services.erp.drafts import DraftValidationError, get_action


@frappe.whitelist(methods=["POST"])
def confirm(action_request: str, token: str) -> dict:
	settings = require_access()
	limits.check_simple_rate("confirm", 30, 60)
	name = cstr(action_request)[:140]
	pending_action = frappe.db.get_value(ACTION_REQUEST, name, "action")
	pending_spec = get_action(pending_action) if pending_action else None
	cap = (pending_spec or {}).get("capability", "draft_actions_enabled")
	if not settings.get(cap) or not capability_allowed(settings, cap):
		frappe.throw(_("This Chikku AI feature is turned off for you by your administrator."), frappe.PermissionError)
	try:
		row, params = tokens.claim(name, cstr(token))
	except (tokens.ConfirmationError, frappe.DoesNotExistError) as e:
		return {"ok": False, "message": str(e) or _("This action can't be confirmed.")}

	spec = get_action(row.action)
	if not spec:
		tokens.finish(name, status="Failed", error="Unknown action")
		audit("unknown_action", "High", conversation=row.conversation, details={"action": row.action})
		return {"ok": False, "message": _("This action is not supported.")}

	frappe.db.savepoint("chikku_action")
	try:
		doc = spec["execute"](settings, params)
	except (DraftValidationError, frappe.ValidationError, frappe.PermissionError, frappe.LinkValidationError,
			frappe.DoesNotExistError) as e:
		frappe.db.rollback(save_point="chikku_action")
		msg = frappe.utils.strip_html(cstr(e)) or _("The draft could not be created.")
		frappe.clear_messages()
		tokens.finish(name, status="Failed", error=msg)
		audit("action_failed", "Warning", conversation=row.conversation, reference_doctype=spec["doctype"],
			  details={"action_request": name, "action": row.action, "error": msg[:300]}, durable=False)
		return {"ok": False, "message": msg}
	except Exception:
		frappe.db.rollback(save_point="chikku_action")
		frappe.clear_messages()
		frappe.log_error(title=f"Chikku AI action {row.action} failed")
		tokens.finish(name, status="Failed", error="Internal error")
		audit("action_failed", "High", conversation=row.conversation, details={"action_request": name},
			  durable=False)
		return {"ok": False, "message": _("Something went wrong. The error was logged.")}

	tokens.finish(name, status="Done", result_doctype=doc.doctype, result_name=doc.name)
	audit("action_executed", "Info", conversation=row.conversation, reference_doctype=doc.doctype,
		  reference_name=doc.name, details={"action_request": name, "action": row.action, "docstatus": doc.docstatus})
	result = {"ok": True, "doctype": doc.doctype, "name": doc.name, "docstatus": doc.docstatus,
			  "action": row.action, "url": get_url_to_form(doc.doctype, doc.name).replace("/app/", "/desk/")}
	frappe.publish_realtime(REALTIME_EVENT, {"type": "action_done", "action_request": name, **result},
							user=frappe.session.user, after_commit=True)
	return result


@frappe.whitelist(methods=["POST"])
def cancel(action_request: str) -> dict:
	require_access()
	try:
		tokens.cancel(cstr(action_request)[:140])
	except frappe.DoesNotExistError:
		return {"ok": False}
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def reissue(action_request: str) -> dict:
	"""After a page reload the browser no longer holds the token; the owner can get a fresh one."""
	require_access()
	name = cstr(action_request)[:140]
	try:
		token = tokens.reissue_token(name)
	except (tokens.ConfirmationError, frappe.DoesNotExistError) as e:
		return {"ok": False, "message": str(e)}
	row = frappe.db.get_value(ACTION_REQUEST, name, ["status", "expires_at"], as_dict=True)
	return {"ok": True, "token": token, "expires_at": str(row.expires_at)}


@frappe.whitelist(methods=["GET", "POST"])
def status(action_request: str) -> dict:
	require_access()
	row = frappe.db.get_value(ACTION_REQUEST, cstr(action_request)[:140],
							  ["user", "status", "expires_at", "result_doctype", "result_name", "error"], as_dict=True)
	if not row or row.user != frappe.session.user:
		return {"ok": False}
	return {"ok": True, "status": row.status, "expires_at": str(row.expires_at), "result_doctype": row.result_doctype,
			"result_name": row.result_name, "error": row.error}
