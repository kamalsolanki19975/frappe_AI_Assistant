"""AI Activity Centre API.

Users see their own activity. Chikku AI Managers / System Managers can additionally list other users'
operational metadata (type, status, time, user, record name) — never conversation titles or contents.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, getdate

from frappe_ai_assistant.frappe_ai_assistant.security.access import is_admin, require_access

SOURCES = {
	"Conversation": ("Chikku AI Conversation", "user", ["title", "status", "message_count"], "status"),
	"Report / Excel": ("Chikku AI Job", "user", ["title", "job_type", "status", "output_url", "summary", "error", "retryable"], "status"),
	"Import": ("Chikku AI Import Session", "user", ["title", "status", "reference_doctype", "success_count", "failed_count", "error"], "status"),
	"Knowledge": ("Chikku AI Knowledge Document", "owner", ["title", "status", "chunk_count", "error"], "status"),
	"Draft Action": ("Chikku AI Action Request", "user", ["summary", "status", "result_doctype", "result_name", "error"], "status"),
	"Workflow Proposal": ("Chikku AI Workflow Proposal", "user", ["title", "status", "proposal_type", "reference_doctype"], "status"),
	"Alert": ("Chikku AI Alert", "user", ["title", "status", "rule", "record_count"], "status"),
}
PRIVATE_FOR_OTHERS = {"Conversation": ["title"], "Draft Action": ["summary", "error"], "Report / Excel": ["title", "summary", "output_url"],
					  "Import": ["title", "error"], "Alert": ["title"]}
SUCCESS = {"Completed", "Indexed", "Done", "Applied (Inactive)", "Acknowledged", "Active"}
FAILED = {"Failed", "Expired", "Partially Completed"}


@frappe.whitelist(methods=["GET", "POST"])
def list_activity(kind: str | None = None, status: str | None = None, from_date: str | None = None,
				  to_date: str | None = None, user: str | None = None, start: int = 0, page_length: int = 30) -> dict:
	require_access()
	admin = is_admin()
	target_user = cstr(user) or frappe.session.user
	if target_user != frappe.session.user and not admin:
		frappe.throw(_("You can only view your own activity."), frappe.PermissionError)
	kinds = [kind] if kind in SOURCES else list(SOURCES)
	rows = []
	page_length = min(cint(page_length) or 30, 100)
	for k in kinds:
		doctype, owner_field, fields, status_field = SOURCES[k]
		if not frappe.has_permission(doctype, "read"):
			continue
		filters = {owner_field: target_user}
		if from_date:
			filters["creation"] = [">=", getdate(from_date)]
		if to_date:
			filters.setdefault("modified", ["<=", f"{getdate(to_date)} 23:59:59"])
		if status == "failed":
			filters[status_field] = ["in", list(FAILED)]
		elif status == "completed":
			filters[status_field] = ["in", list(SUCCESS)]
		if target_user == frappe.session.user:
			data = frappe.get_list(doctype, filters=filters, fields=["name", "creation", "modified", *fields],
								   order_by="modified desc", limit_page_length=cint(start) + page_length)
		else:
			# Administrator view of another user: operational metadata only (explicit field whitelist).
			safe = [f for f in fields if f not in PRIVATE_FOR_OTHERS.get(k, [])]
			data = frappe.get_all(doctype, filters=filters, fields=["name", "creation", "modified", *safe],
								  order_by="modified desc", limit_page_length=cint(start) + page_length)
		for d in data:
			title = d.get("title") or d.get("summary") or (_("Private conversation") if k == "Conversation" else d.name)
			rows.append({"kind": k, "doctype": doctype, "name": d.name, "title": cstr(title)[:140], "status": d.get(status_field),
						 "modified": str(d.modified), "creation": str(d.creation),
						 "detail": {f: d.get(f) for f in fields if f not in ("title", "summary", "status") and f in d},
						 "retry": _retry_kind(k, d) if target_user == frappe.session.user else None})
	rows.sort(key=lambda r: r["modified"], reverse=True)
	return {"rows": rows[cint(start): cint(start) + page_length], "has_more": len(rows) > cint(start) + page_length,
			"is_admin": admin, "user": target_user, "kinds": list(SOURCES)}


def _retry_kind(kind, d):
	if kind == "Import" and d.get("status") in ("Failed", "Partially Completed"):
		return "import"
	if kind == "Knowledge" and d.get("status") in ("Failed", "Indexed"):
		return "reindex"
	if kind == "Report / Excel" and d.get("status") == "Failed" and d.get("job_type") in ("Excel Report", "Analytics Export"):
		return "report"
	return None


@frappe.whitelist(methods=["POST"])
def retry(kind: str, name: str) -> dict:
	"""Only safe, idempotent retries: import (failed rows only, re-validated), knowledge re-index, report regeneration."""
	require_access()
	if kind == "import":
		from frappe_ai_assistant.frappe_ai_assistant.imports.studio import retry_failed

		return retry_failed(name)
	if kind == "reindex":
		doc = frappe.get_doc("Chikku AI Knowledge Document", name)
		doc.check_permission("write")
		doc.db_set("status", "Pending")
		frappe.enqueue("frappe_ai_assistant.frappe_ai_assistant.knowledge.index.index_document", queue="long", timeout=900,
					   job_id=f"chikku_kb::{name}", deduplicate=True, enqueue_after_commit=True, name=name)
		return {"ok": True}
	if kind == "report":
		import json

		job = frappe.get_doc("Chikku AI Job", name)
		if job.user != frappe.session.user:
			raise frappe.DoesNotExistError
		from frappe_ai_assistant.frappe_ai_assistant.excel.service import generate_report

		p = json.loads(job.params_json or "{}")
		res = generate_report(title=job.title, conversation=job.conversation, metric=p.get("metric"),
							  metric_params=p.get("metric_params"), doctype=p.get("doctype"), filters=p.get("filters"),
							  fields=p.get("fields"))
		return {"ok": True, "file": res["file"]}
	frappe.throw(_("This activity cannot be retried."))


@frappe.whitelist(methods=["GET", "POST"])
def admin_overview() -> dict:
	"""Usage and operational metadata for administrators (no conversation content)."""
	require_access()
	if not is_admin():
		frappe.throw(_("Only Chikku AI Managers can view this."), frappe.PermissionError)
	from frappe_ai_assistant.frappe_ai_assistant.api.admin import usage_summary

	jobs = frappe.db.sql("""select job_type, status, count(*) n from `tabChikku AI Job`
		where creation >= date_sub(now(), interval 30 day) group by job_type, status""", as_dict=True)
	imports = frappe.db.sql("""select status, count(*) n, sum(success_count) ok, sum(failed_count) failed
		from `tabChikku AI Import Session` where creation >= date_sub(now(), interval 30 day) group by status""", as_dict=True)
	return {**usage_summary(), "jobs": jobs, "imports": imports}
