"""Workflow proposal approval (administrators) and alert management (users)."""

from __future__ import annotations

import frappe
from frappe import _

from frappe_ai_assistant.frappe_ai_assistant.security.access import is_admin, require_access
from frappe_ai_assistant.frappe_ai_assistant.security.tokens import create_action_request
from frappe_ai_assistant.frappe_ai_assistant.workflows import proposals


@frappe.whitelist(methods=["POST"])
def request_apply(proposal: str) -> dict:
	"""Administrator clicked 'Create (inactive)'. Returns a single-use token to that browser only."""
	settings = require_access("workflow_intelligence_enabled")
	if not is_admin():
		frappe.throw(_("Only Chikku AI Managers or System Managers can apply proposals."), frappe.PermissionError)
	doc = frappe.get_doc(proposals.PROPOSAL, proposal)
	block = proposals.proposal_block(doc)
	if not block["can_apply"]:
		frappe.throw(_("This proposal cannot be applied (already handled or has errors)."))
	name, token = create_action_request(action=proposals.APPLY_ACTION, params={"proposal": doc.name, "spec": doc.spec_json},
										preview=block["spec"], summary=f"Create inactive {doc.proposal_type}: {doc.title}",
										target_doctype=doc.proposal_type, conversation=None, message=None,
										expiry_minutes=settings.action_expiry_minutes or 10)
	return {"action_request": name, "token": token}


@frappe.whitelist(methods=["POST"])
def reject(proposal: str) -> dict:
	require_access("workflow_intelligence_enabled")
	if not is_admin():
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	doc = frappe.get_doc(proposals.PROPOSAL, proposal)
	if doc.status == "Draft":
		doc.db_set("status", "Rejected")
	return {"status": doc.status}


@frappe.whitelist(methods=["GET", "POST"])
def my_alerts(status: str | None = None) -> list:
	require_access()
	filters = {"user": frappe.session.user}
	if status:
		filters["status"] = status
	return frappe.get_list("Chikku AI Alert", filters=filters, fields=["name", "title", "rule", "status", "record_count", "creation"],
						   order_by="creation desc", limit_page_length=30)


@frappe.whitelist(methods=["POST"])
def set_alert_status(alert: str, status: str) -> dict:
	require_access()
	from frappe_ai_assistant.frappe_ai_assistant.notifications.alerts import set_status

	set_status(alert, status)
	return {"ok": True}
