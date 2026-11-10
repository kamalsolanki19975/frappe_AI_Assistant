"""Opt-in proactive alerts. Users subscribe to fixed, verifiable rules; a scheduler job evaluates each
due subscription AS THAT USER (their permissions, companies, warehouses), suppresses duplicates
(same result as last time), caps alerts per day, and records every alert with its source trace."""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime, today

from frappe_ai_assistant.frappe_ai_assistant import REALTIME_EVENT
from frappe_ai_assistant.frappe_ai_assistant.analytics import metrics

SUB = "Chikku AI Alert Subscription"
ALERT = "Chikku AI Alert"


def _pending_approvals(sub):
	rows = frappe.get_list("Workflow Action", filters={"status": "Open"},
						   fields=["name", "reference_doctype", "reference_name", "workflow_state"], limit_page_length=200)
	mine = [r for r in rows if frappe.has_permission(r.reference_doctype, "read", r.reference_name)]
	return {"title": _("{0} document(s) waiting for your approval").format(len(mine)),
			"records": [{"doctype": r.reference_doctype, "name": r.reference_name, "detail": r.workflow_state} for r in mine],
			"trace": {"source_doctypes": ["Workflow Action"], "method": "Open workflow actions you can act on."}}


def _failed_imports(sub):
	rows = frappe.get_list("Chikku AI Import Session", filters={"status": ["in", ["Failed", "Partially Completed"]],
																 "modified": [">", add_to_date(now_datetime(), days=-1)]},
						   fields=["name", "title", "status", "failed_count"], limit_page_length=50)
	return {"title": _("{0} import(s) had failures in the last day").format(len(rows)),
			"records": [{"doctype": "Chikku AI Import Session", "name": r.name, "detail": f"{r.status}, {r.failed_count} failed"} for r in rows],
			"trace": {"source_doctypes": ["Chikku AI Import Session"], "method": "Your Chikku imports that failed or partly failed in the last 24 hours."}}


def _metric_rule(metric, title_fmt, doctype_key="name"):
	def run(sub):
		res = metrics.run(metric, {"company": sub.company, "min_days": cint(sub.threshold_days), "limit": 100})
		keys = [c["key"] for c in res["columns"]]
		recs = []
		for r in res["rows"]:
			row = dict(zip(keys, r))
			ref = (res.get("refs") or [{}])[0].get("doctype")
			recs.append({"doctype": ref or ("Item" if doctype_key == "item_code" else ""),
						 "name": row.get(doctype_key) or row.get("item_code"),
						 "detail": ", ".join(f"{k}: {v}" for k, v in list(row.items())[1:4])})
		return {"title": title_fmt.format(len(recs)), "records": recs, "trace": res["trace"]}
	return run


RULES = {
	"Overdue Sales Orders": _metric_rule("delayed_sales_orders", "{0} Sales Order(s) are past their delivery date"),
	"Delayed Purchase Deliveries": _metric_rule("delayed_purchase_orders", "{0} Purchase Order(s) are overdue for receipt"),
	"Low Stock": _metric_rule("stock_shortages", "{0} item-warehouse(s) are short of stock", "item_code"),
	"Pending Approvals": _pending_approvals,
	"Failed Imports": _failed_imports,
}


def is_due(sub) -> bool:
	if not sub.last_run:
		return True
	delta = {"Hour": 1 / 24, "Day": 1}.get(sub.frequency, 1)
	return get_datetime(sub.last_run) <= add_to_date(now_datetime(), days=-delta, seconds=60)


def evaluate(sub, *, force: bool = False) -> dict | None:
	"""Evaluate one subscription as the current session user (must be the owner). Returns the alert or None."""
	if sub.user != frappe.session.user:
		raise frappe.PermissionError
	settings = frappe.get_cached_doc("Chikku AI Settings")
	if not settings.enabled or not settings.notifications_enabled:
		return None
	result = RULES[sub.rule](sub)
	records = result["records"][:100]
	signature = hashlib.sha256(json.dumps(sorted(f"{r['doctype']}|{r['name']}" for r in records)).encode()).hexdigest()[:32]
	frappe.db.set_value(SUB, sub.name, {"last_run": now_datetime()}, update_modified=False)
	if not records:
		frappe.db.set_value(SUB, sub.name, "last_signature", None, update_modified=False)
		return None
	if signature == sub.last_signature and not force:
		return None  # same situation as last alert: don't repeat it
	sent_today = frappe.db.count(ALERT, {"user": sub.user, "creation": [">=", today()]})
	if sent_today >= (cint(settings.max_alerts_per_user_per_day) or 10):
		return None
	alert = frappe.get_doc({"doctype": ALERT, "title": result["title"][:140], "rule": sub.rule, "status": "New",
							"user": sub.user, "subscription": sub.name, "record_count": len(records), "signature": signature,
							"message": result["title"], "records_json": json.dumps(records[:100], default=str),
							"trace_json": json.dumps(result["trace"], default=str)})
	alert.insert(ignore_permissions=True)  # code-written, owned by the subscriber
	frappe.db.set_value(SUB, sub.name, "last_signature", signature, update_modified=False)
	first = records[0]
	frappe.get_doc({"doctype": "Notification Log", "for_user": sub.user, "type": "Alert", "subject": f"Chikku AI: {result['title']}",
					"email_content": "<br>".join(frappe.utils.escape_html(f"{r['doctype']} {r['name']} — {r.get('detail', '')}")
												 for r in records[:20]),
					"document_type": ALERT, "document_name": alert.name, "from_user": "Administrator"}).insert(ignore_permissions=True)
	frappe.publish_realtime(REALTIME_EVENT, {"type": "alert", "alert": alert.name, "title": alert.title,
											 "count": len(records), "first": first}, user=sub.user)
	return {"alert": alert.name, "title": alert.title, "records": records[:20], "trace": result["trace"]}


def run_due_subscriptions():
	"""Scheduler (hourly). Each subscription is evaluated as its owner; failures are isolated."""
	settings = frappe.get_cached_doc("Chikku AI Settings")
	if not settings.enabled or not settings.notifications_enabled:
		return
	from frappe_ai_assistant.frappe_ai_assistant.security.access import has_access

	subs = frappe.get_all(SUB, filters={"enabled": 1}, fields=["name", "user", "rule", "frequency", "company",
															   "threshold_days", "last_run", "last_signature"])
	for sub in subs:
		if not is_due(sub):
			continue
		try:
			frappe.set_user(sub.user)
			if has_access(sub.user):
				evaluate(sub)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Chikku AI alert {sub.name} failed")
		finally:
			frappe.set_user("Administrator")


def set_status(alert: str, status: str):
	if status not in ("Acknowledged", "Dismissed"):
		frappe.throw(_("Invalid status."))
	row = frappe.db.get_value(ALERT, alert, ["user"], as_dict=True)
	if not row or row.user != frappe.session.user:
		raise frappe.DoesNotExistError(_("Alert not found."))
	frappe.db.set_value(ALERT, alert, "status", status)
