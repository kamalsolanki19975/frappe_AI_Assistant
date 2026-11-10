"""Row-level rules for Chikku AI DocTypes (registered in hooks.py).

Conversations and messages are private to their owner. No role, including managers and
System Managers, can read another user's conversation through the Desk or the API.
(Administrator bypasses Frappe permission hooks by design.)
"""

import frappe

from frappe_ai_assistant.frappe_ai_assistant import CONVERSATION


def _is_manager(user):
	from frappe_ai_assistant.frappe_ai_assistant.security.access import is_admin

	return is_admin(user)


def conversation_has_permission(doc, ptype="read", user=None):
	user = user or frappe.session.user
	return doc.user == user


def message_has_permission(doc, ptype="read", user=None):
	user = user or frappe.session.user
	return frappe.db.get_value(CONVERSATION, doc.conversation, "user") == user


def action_request_has_permission(doc, ptype="read", user=None):
	user = user or frappe.session.user
	if doc.user == user:
		return True
	return ptype == "read" and _is_manager(user)


def conversation_query(user=None):
	user = user or frappe.session.user
	return f"`tab{CONVERSATION}`.`user` = {frappe.db.escape(user)}"


def message_query(user=None):
	user = user or frappe.session.user
	return (f"`tabChikku AI Message`.`conversation` in "
			f"(select name from `tab{CONVERSATION}` where `user` = {frappe.db.escape(user)})")


def action_request_query(user=None):
	user = user or frappe.session.user
	if _is_manager(user):
		return ""
	return f"`tabChikku AI Action Request`.`user` = {frappe.db.escape(user)}"


# ---------------------------------------------------------------- R2 records
def _owner_or_manager(doc, ptype, user, field="user"):
	user = user or frappe.session.user
	if doc.get(field) == user:
		return True
	return ptype in ("read", "report") and _is_manager(user)


def job_has_permission(doc, ptype="read", user=None):
	return _owner_or_manager(doc, ptype, user)


def import_session_has_permission(doc, ptype="read", user=None):
	return _owner_or_manager(doc, ptype, user)


def alert_has_permission(doc, ptype="read", user=None):
	return doc.user == (user or frappe.session.user)


def _user_query(doctype, user, manager_sees_all=True):
	user = user or frappe.session.user
	if manager_sees_all and _is_manager(user):
		return ""
	return f"`tab{doctype}`.`user` = {frappe.db.escape(user)}"


def job_query(user=None):
	return _user_query("Chikku AI Job", user)


def import_session_query(user=None):
	return _user_query("Chikku AI Import Session", user)


def alert_query(user=None):
	return _user_query("Chikku AI Alert", user, manager_sees_all=False)


def knowledge_has_permission(doc, ptype="read", user=None):
	"""Owner and Chikku managers: full. Others: read only, per the document's visibility."""
	from frappe_ai_assistant.frappe_ai_assistant.security.access import has_access

	user = user or frappe.session.user
	if doc.owner == user or _is_manager(user):
		return True
	if ptype not in ("read", "select") or not has_access(user):
		return False
	if doc.visibility == "All Chikku AI Users":
		return True
	if doc.visibility == "Selected Roles":
		return bool({r.role for r in doc.roles or []} & set(frappe.get_roles(user)))
	return False


def knowledge_query(user=None):
	from frappe_ai_assistant.frappe_ai_assistant.security.access import has_access

	user = user or frappe.session.user
	if _is_manager(user):
		return ""
	t = "`tabChikku AI Knowledge Document`"
	cond = f"{t}.owner = {frappe.db.escape(user)}"
	if has_access(user):
		roles = ", ".join(frappe.db.escape(r) for r in frappe.get_roles(user)) or "''"
		cond += (f" or {t}.visibility = 'All Chikku AI Users' or ({t}.visibility = 'Selected Roles' and {t}.name in "
				 f"(select parent from `tabChikku AI Knowledge Role` where role in ({roles})))")
	return f"({cond})"
