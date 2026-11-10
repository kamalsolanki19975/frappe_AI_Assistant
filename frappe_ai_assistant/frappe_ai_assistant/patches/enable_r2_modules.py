"""R2: modules that were "not implemented" in R1 were stored as disabled. Enable the implemented ones once.
Administrators can turn any of them off again in Chikku AI Settings."""

import frappe

FLAGS = ("excel_enabled", "import_enabled", "analytics_enabled", "document_intelligence_enabled", "knowledge_enabled",
		 "workflow_intelligence_enabled", "voice_enabled", "image_processing_enabled", "notifications_enabled")


def execute():
	if not frappe.db.exists("DocType", "Chikku AI Settings"):
		return
	for f in FLAGS:
		frappe.db.set_single_value("Chikku AI Settings", f, 1)
	frappe.clear_document_cache("Chikku AI Settings", "Chikku AI Settings")
