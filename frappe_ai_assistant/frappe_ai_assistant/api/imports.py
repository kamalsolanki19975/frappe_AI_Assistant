"""Import Studio endpoints used by the mapping / preview components in the chat panel."""

from __future__ import annotations

import json

import frappe

from frappe_ai_assistant.frappe_ai_assistant.imports import studio
from frappe_ai_assistant.frappe_ai_assistant.security import limits
from frappe_ai_assistant.frappe_ai_assistant.security.access import require_access


def _json(v, default):
	if v in (None, ""):
		return default
	return json.loads(v) if isinstance(v, str) else v


@frappe.whitelist(methods=["GET", "POST"])
def get_session(session: str) -> dict:
	require_access("import_enabled")
	s = studio.owned_session(session)
	return studio.preview_block(s) if s.validation_json and s.status != "Mapping" else studio.mapping_block(s)


@frappe.whitelist(methods=["POST"])
def update_mapping(session: str, mapping: str | list, defaults: str | dict | None = None, import_type: str | None = None,
				   parent_key_column: str | None = None) -> dict:
	require_access("import_enabled")
	return studio.update_mapping(session, _json(mapping, []), _json(defaults, {}), import_type, parent_key_column)


@frappe.whitelist(methods=["POST"])
def validate(session: str) -> dict:
	"""Validation runs in a background job; the result arrives as an `import_status` realtime event."""
	require_access("import_enabled")
	limits.check_simple_rate("import_validate", 20, 3600)
	s = studio.owned_session(session)
	if s.status not in ("Mapping", "Validated"):
		frappe.throw(frappe._("This import can no longer be validated."))
	frappe.enqueue("frappe_ai_assistant.frappe_ai_assistant.api.imports.validate_job", queue="long", timeout=1800,
				   job_id=f"chikku_import_validate::{s.name}", deduplicate=True, session=s.name)
	return {"queued": True}


def validate_job(session: str):
	from frappe_ai_assistant.frappe_ai_assistant import REALTIME_EVENT

	user = frappe.session.user
	try:
		block = studio.validate(session, frappe.get_cached_doc("Chikku AI Settings"))
		frappe.db.commit()
		frappe.publish_realtime(REALTIME_EVENT, {"type": "import_status", "session": session, "status": "Validated",
												 "preview": block}, user=user)
	except Exception as e:
		frappe.db.rollback()
		frappe.db.set_value(studio.SESSION, session, "status", "Mapping")
		frappe.db.commit()
		if not isinstance(e, (frappe.ValidationError, frappe.DoesNotExistError)):
			frappe.log_error(title="Chikku AI import validation failed")
		frappe.publish_realtime(REALTIME_EVENT, {"type": "import_status", "session": session, "status": "Mapping",
												 "error": frappe.utils.strip_html(str(e))[:300] or "Validation failed."}, user=user)


@frappe.whitelist(methods=["POST"])
def request_confirmation(session: str) -> dict:
	"""Token goes to the browser that clicked; the model never sees it."""
	settings = require_access("import_enabled")
	return studio.request_confirmation(session, settings)


@frappe.whitelist(methods=["POST"])
def save_template(session: str, title: str) -> dict:
	require_access("import_enabled")
	s = studio.owned_session(session)
	headers = [c["header"] for c in json.loads(s.columns_json)]
	sig = studio._hash([studio._norm(h) for h in headers])
	existing = frappe.get_list("Chikku AI Import Template", filters={"reference_doctype": s.reference_doctype,
																	 "header_signature": sig}, pluck="name")
	doc = frappe.get_doc("Chikku AI Import Template", existing[0]) if existing else frappe.new_doc("Chikku AI Import Template")
	doc.update({"title": str(title)[:140] or s.title, "reference_doctype": s.reference_doctype, "header_signature": sig,
				"mapping_json": s.mapping_json, "defaults_json": s.defaults_json, "user": frappe.session.user})
	doc.flags.ignore_permissions = doc.is_new()  # template creation is code-driven; owner-only afterwards
	doc.save(ignore_permissions=doc.is_new())
	return {"template": doc.name}


@frappe.whitelist(methods=["POST"])
def back_to_mapping(session: str) -> dict:
	require_access("import_enabled")
	s = studio.owned_session(session)
	if s.status not in ("Validated", "Mapping"):
		frappe.throw(frappe._("This import can no longer be changed."))
	s.db_set({"status": "Mapping", "validation_json": None, "mapping_hash": None})
	return studio.mapping_block(s)


@frappe.whitelist(methods=["POST"])
def retry_failed(session: str) -> dict:
	require_access("import_enabled")
	return studio.retry_failed(session)


@frappe.whitelist(methods=["POST"])
def cancel(session: str) -> dict:
	require_access("import_enabled")
	s = studio.owned_session(session)
	if s.status in ("Mapping", "Validating", "Validated"):
		s.db_set("status", "Cancelled")
	return {"status": s.status}


@frappe.whitelist(methods=["GET", "POST"])
def history(start: int = 0) -> list:
	require_access("import_enabled")
	return frappe.get_list(studio.SESSION, filters={"user": frappe.session.user},
						   fields=["name", "title", "status", "reference_doctype", "success_count", "failed_count", "modified"],
						   order_by="modified desc", limit_start=int(start or 0), limit_page_length=20)
