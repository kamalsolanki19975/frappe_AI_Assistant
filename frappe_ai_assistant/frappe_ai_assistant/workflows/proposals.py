"""Workflow Intelligence: declarative proposals, validation, administrator-approved creation.

Proposals never run code. Conditions use a restricted expression language checked with Python's AST
(only `doc.<existing field>`, literals, comparisons, and/or/not). Applying a proposal creates the native
Workflow with is_active = 0 (or a Notification with enabled = 0); an administrator activates it in the
standard Frappe screens. Nothing is ever activated automatically.
"""

from __future__ import annotations

import ast
import json

import frappe
from frappe import _
from frappe.utils import cint, cstr

from frappe_ai_assistant.frappe_ai_assistant.security.access import is_admin
from frappe_ai_assistant.frappe_ai_assistant.services.erp.policy import DENY_DOCTYPES

PROPOSAL = "Chikku AI Workflow Proposal"
APPLY_ACTION = "workflow.apply"
ALLOWED_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Compare, ast.Eq, ast.NotEq,
				 ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Constant, ast.Attribute, ast.Name, ast.Load,
				 ast.List, ast.Tuple)


class ProposalError(frappe.ValidationError):
	pass


def check_condition(expr: str | None, doctype: str) -> list[str]:
	if not expr:
		return []
	try:
		tree = ast.parse(expr, mode="eval")
	except SyntaxError:
		return [f"Condition '{expr}' is not valid."]
	meta = frappe.get_meta(doctype)
	problems = []
	for node in ast.walk(tree):
		if not isinstance(node, ALLOWED_NODES):
			problems.append(f"Condition uses a disallowed construct ({type(node).__name__}).")
			break
		if isinstance(node, ast.Name) and node.id != "doc":
			problems.append(f"Condition may only refer to doc.<field>, not '{node.id}'.")
		if isinstance(node, ast.Attribute):
			if not (isinstance(node.value, ast.Name) and node.value.id == "doc"):
				problems.append("Only doc.<field> attributes are allowed in conditions.")
			elif not meta.has_field(node.attr) and node.attr not in ("name", "owner", "docstatus"):
				problems.append(f"{doctype} has no field '{node.attr}'.")
	return problems


def _require_admin():
	if not is_admin():
		raise ProposalError(_("Only Chikku AI Managers or System Managers can work with workflow proposals."))


def validate_workflow_spec(spec: dict) -> tuple[dict, list[dict]]:
	doctype = cstr(spec.get("doctype"))
	issues = []
	if not frappe.db.exists("DocType", doctype) or doctype in DENY_DOCTYPES or doctype.startswith("Chikku AI"):
		raise ProposalError(_("DocType '{0}' is not available for workflows.").format(doctype[:80]))
	meta = frappe.get_meta(doctype)
	if meta.istable or meta.issingle:
		raise ProposalError(_("Workflows need a normal (non-child, non-single) DocType."))
	states = spec.get("states") or []
	transitions = spec.get("transitions") or []
	if not 2 <= len(states) <= 15:
		raise ProposalError(_("A workflow needs between 2 and 15 states."))
	names = [cstr(s.get("state")).strip()[:100] for s in states]
	if len(set(names)) != len(names) or not all(names):
		raise ProposalError(_("State names must be unique and not empty."))
	for s in states:
		ds = cint(s.get("doc_status"))
		if ds not in (0, 1, 2):
			raise ProposalError(_("doc_status must be 0 (draft), 1 (submitted) or 2 (cancelled)."))
		if ds and not meta.is_submittable:
			issues.append({"level": "error", "message": f"State '{s['state']}' uses doc_status {ds} but {doctype} is not submittable."})
		role = s.get("allow_edit")
		if not role or not frappe.db.exists("Role", role):
			issues.append({"level": "error", "message": f"State '{s.get('state')}': role '{role}' does not exist."})
	for t in transitions:
		for k in ("state", "next_state"):
			if t.get(k) not in names:
				issues.append({"level": "error", "message": f"Transition refers to unknown state '{t.get(k)}'."})
		if not t.get("action"):
			issues.append({"level": "error", "message": "Every transition needs an action name (e.g. Approve)."})
		if not frappe.db.exists("Role", t.get("allowed") or ""):
			issues.append({"level": "error", "message": f"Transition role '{t.get('allowed')}' does not exist."})
		elif not frappe.get_all("DocPerm", {"parent": doctype, "role": t["allowed"]}, limit=1) and \
				not frappe.get_all("Custom DocPerm", {"parent": doctype, "role": t["allowed"]}, limit=1):
			issues.append({"level": "warning", "message": f"Role '{t['allowed']}' has no permission on {doctype}; users with only this role cannot act."})
		for p in check_condition(t.get("condition"), doctype):
			issues.append({"level": "error", "message": p})
	reachable = {names[0]} | {t.get("next_state") for t in transitions}
	for n in names[1:]:
		if n not in reachable:
			issues.append({"level": "warning", "message": f"State '{n}' cannot be reached by any transition."})
	active = frappe.get_all("Workflow", {"document_type": doctype, "is_active": 1}, pluck="name")
	if active:
		issues.append({"level": "warning", "message": f"{doctype} already has an active workflow ({', '.join(active)}). "
					   "The new one is created inactive; activating it will replace the current one."})
	clean = {"kind": "Workflow", "doctype": doctype, "title": cstr(spec.get("title") or f"{doctype} Approval")[:100],
			 "states": [{"state": n, "doc_status": cint(s.get("doc_status")), "allow_edit": s.get("allow_edit")}
						for n, s in zip(names, states)],
			 "transitions": [{"state": t.get("state"), "action": cstr(t.get("action"))[:100], "next_state": t.get("next_state"),
							  "allowed": t.get("allowed"), "condition": cstr(t.get("condition") or "")[:500] or None,
							  "allow_self_approval": cint(t.get("allow_self_approval", 1))} for t in transitions]}
	return clean, issues


NOTIFY_EVENTS = {"New", "Save", "Submit", "Cancel", "Days After", "Days Before", "Value Change"}


def validate_notification_spec(spec: dict) -> tuple[dict, list[dict]]:
	doctype = cstr(spec.get("doctype"))
	if not frappe.db.exists("DocType", doctype) or doctype in DENY_DOCTYPES or doctype.startswith("Chikku AI"):
		raise ProposalError(_("DocType '{0}' is not available for notifications.").format(doctype[:80]))
	meta = frappe.get_meta(doctype)
	issues = []
	event = spec.get("event")
	if event not in NOTIFY_EVENTS:
		raise ProposalError(_("Event must be one of {0}.").format(", ".join(sorted(NOTIFY_EVENTS))))
	date_field = spec.get("date_field")
	if event in ("Days After", "Days Before") and (not date_field or meta.get_field(date_field) is None
												   or meta.get_field(date_field).fieldtype not in ("Date", "Datetime")):
		issues.append({"level": "error", "message": "Days After/Before needs a date field of the DocType."})
	roles = [r for r in spec.get("recipient_roles") or [] if frappe.db.exists("Role", r)]
	if not roles:
		issues.append({"level": "error", "message": "Choose at least one existing role to notify."})
	for p in check_condition(spec.get("condition"), doctype):
		issues.append({"level": "error", "message": p})
	clean = {"kind": "Notification", "doctype": doctype, "title": cstr(spec.get("title") or f"{doctype} alert")[:100],
			 "event": event, "date_field": date_field, "days": cint(spec.get("days")),
			 "condition": cstr(spec.get("condition") or "")[:500] or None, "recipient_roles": roles,
			 "subject": cstr(spec.get("subject") or "")[:140] or f"{doctype} {{{{ doc.name }}}} needs attention",
			 "message": cstr(spec.get("message") or "")[:2000] or "Please review {{ doc.name }}."}
	return clean, issues


def create_proposal(spec: dict, explanation: str, conversation: str | None = None) -> dict:
	_require_admin()
	kind = spec.get("kind") or "Workflow"
	clean, issues = validate_workflow_spec(spec) if kind == "Workflow" else validate_notification_spec(spec)
	doc = frappe.get_doc({"doctype": PROPOSAL, "title": clean["title"], "proposal_type": clean["kind"],
						  "reference_doctype": clean["doctype"], "status": "Draft", "user": frappe.session.user,
						  "explanation": cstr(explanation)[:5000], "spec_json": json.dumps(clean),
						  "validation_json": json.dumps(issues)})
	doc.insert(ignore_permissions=True)  # code-written; managers have read access
	return proposal_block(doc)


def proposal_block(doc) -> dict:
	spec = json.loads(doc.spec_json or "{}")
	issues = json.loads(doc.validation_json or "[]")
	return {"type": "workflow_proposal", "proposal": doc.name, "status": doc.status, "kind": doc.proposal_type,
			"title": doc.title, "doctype": doc.reference_doctype, "spec": spec, "issues": issues,
			"explanation": doc.explanation, "can_apply": doc.status == "Draft" and not any(i["level"] == "error" for i in issues),
			"applied": {"doctype": doc.applied_doctype, "name": doc.applied_name} if doc.applied_name else None}


def revalidate(settings, params: dict):
	_require_admin()
	doc = frappe.get_doc(PROPOSAL, params["proposal"])
	if params.get("spec") and params["spec"] != doc.spec_json:
		raise ProposalError(_("The proposal changed after approval was requested."))
	if doc.status != "Draft":
		raise ProposalError(_("This proposal was already handled."))
	spec = json.loads(doc.spec_json)
	clean, issues = validate_workflow_spec(spec) if doc.proposal_type == "Workflow" else validate_notification_spec(spec)
	if any(i["level"] == "error" for i in issues):
		raise ProposalError(_("The proposal no longer validates: {0}").format("; ".join(i["message"] for i in issues if i["level"] == "error")))
	return doc, clean


def apply_action(settings, params: dict):
	"""Action handler after the administrator confirmed with a token. Creates INACTIVE configuration."""
	doc, spec = revalidate(settings, params)
	if spec["kind"] == "Workflow":
		for s in spec["states"]:
			if not frappe.db.exists("Workflow State", s["state"]):
				frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": s["state"]}).insert()
		for t in spec["transitions"]:
			if not frappe.db.exists("Workflow Action Master", t["action"]):
				frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": t["action"]}).insert()
		wf = frappe.get_doc({"doctype": "Workflow", "workflow_name": spec["title"], "document_type": spec["doctype"],
							 "is_active": 0, "send_email_alert": 0,
							 "states": [{"state": s["state"], "doc_status": str(s["doc_status"]), "allow_edit": s["allow_edit"]}
										for s in spec["states"]],
							 "transitions": [{"state": t["state"], "action": t["action"], "next_state": t["next_state"],
											  "allowed": t["allowed"], "condition": t["condition"],
											  "allow_self_approval": t["allow_self_approval"]} for t in spec["transitions"]]})
		wf.insert()  # needs the administrator's own Workflow create permission
		created = wf
	else:
		n = frappe.get_doc({"doctype": "Notification", "subject": spec["subject"], "document_type": spec["doctype"],
							"event": spec["event"], "enabled": 0, "channel": "System Notification",
							"days_in_advance": spec["days"] or 0, "date_changed": spec["date_field"],
							"condition": spec["condition"], "message": spec["message"],
							"recipients": [{"receiver_by_role": r} for r in spec["recipient_roles"]]})
		n.name = None
		n.insert()
		created = n
	doc.db_set({"status": "Applied (Inactive)", "applied_doctype": created.doctype, "applied_name": created.name})
	return created
