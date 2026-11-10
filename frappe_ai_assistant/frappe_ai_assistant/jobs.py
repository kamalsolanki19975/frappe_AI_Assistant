"""Background jobs. RQ runs each job as the user who enqueued it (frappe.enqueue stores
frappe.session.user and the worker calls frappe.set_user); run_turn re-checks that identity."""

import frappe
from frappe.utils import add_days, cint, now_datetime

from frappe_ai_assistant.frappe_ai_assistant import ACTION_REQUEST, AUDIT_LOG, CONVERSATION, MESSAGE, REALTIME_EVENT, TOOL_CALL, USAGE_LOG


def run_turn_job(message: str):
	from frappe_ai_assistant.frappe_ai_assistant.ai.orchestrator import friendly, run_turn

	try:
		run_turn(message)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Chikku AI turn job failed")
		row = frappe.db.get_value(MESSAGE, message, ["conversation", "owner"], as_dict=True)
		if row:
			frappe.db.set_value(MESSAGE, message, {"status": "Failed", "error_code": "internal"})
			frappe.db.sql(
				f"update `tab{MESSAGE}` set status='Failed', error_code='internal', content=%s "
				f"where idempotency_key=%s",
				(friendly("internal", "en"), f"reply:{message}"),
			)
			frappe.db.commit()
			frappe.publish_realtime(REALTIME_EVENT, {"type": "error", "conversation": row.conversation,
													 "message": message, "text": friendly("internal", "en")},
									user=row.owner)


def expire_action_requests():
	from frappe_ai_assistant.frappe_ai_assistant.security.tokens import expire_stale

	expire_stale()
	frappe.db.commit()


def purge_retention():
	"""Delete Chikku data older than the configured retention periods."""
	settings = frappe.get_cached_doc("Chikku AI Settings")
	if cint(settings.retention_logs_days) > 0:
		cutoff = add_days(now_datetime(), -cint(settings.retention_logs_days))
		frappe.db.delete(TOOL_CALL, {"creation": ["<", cutoff]})
		frappe.db.delete(USAGE_LOG, {"creation": ["<", cutoff]})
	if cint(settings.retention_audit_days) > 0:
		frappe.db.delete(AUDIT_LOG, {"creation": ["<", add_days(now_datetime(), -cint(settings.retention_audit_days))]})
	if cint(settings.retention_messages_days) > 0:
		cutoff = add_days(now_datetime(), -cint(settings.retention_messages_days))
		old = frappe.get_all(CONVERSATION, filters={"modified": ["<", cutoff]}, pluck="name", limit_page_length=500)
		for name in old:
			for f in frappe.get_all("File", filters={"attached_to_doctype": CONVERSATION, "attached_to_name": name},
									pluck="name"):
				frappe.delete_doc("File", f, ignore_permissions=True, force=True)
			frappe.db.delete(MESSAGE, {"conversation": name})
			frappe.db.set_value(ACTION_REQUEST, {"conversation": name}, "conversation", None, update_modified=False)
			frappe.db.delete(CONVERSATION, {"name": name})
	frappe.db.commit()
