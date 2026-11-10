# Copyright (c) 2026, Simpel and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from frappe_ai_assistant.frappe_ai_assistant.doctype.private_document import PrivateDocument


class ChikkuAIKnowledgeDocument(PrivateDocument):
	def validate(self):
		ext = (self.file or "").rsplit(".", 1)[-1].lower()
		if ext not in ("pdf", "docx", "txt", "md", "csv", "xlsx"):
			frappe.throw(_("Use a PDF, DOCX, TXT, MD, CSV or XLSX file."))
		if self.visibility == "Selected Roles" and not self.roles:
			frappe.throw(_("Choose at least one role, or change who can use this document."))
		if self.has_value_changed("file") or self.is_new():
			self.status = "Pending"

	def on_update(self):
		if self.status == "Pending":
			frappe.enqueue("frappe_ai_assistant.frappe_ai_assistant.knowledge.index.index_document", queue="long", timeout=900,
						   job_id=f"chikku_kb::{self.name}", deduplicate=True, enqueue_after_commit=True, name=self.name)

	def on_trash(self):
		frappe.db.delete("Chikku AI Knowledge Chunk", {"knowledge_document": self.name})
