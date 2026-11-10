# Copyright (c) 2026, Simpel and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class ChikkuAIProviderConfiguration(Document):
	def validate(self):
		from frappe_ai_assistant.frappe_ai_assistant.providers.base import ProviderNotConfigured
		from frappe_ai_assistant.frappe_ai_assistant.providers.router import validate_base_url

		self.base_url = (self.base_url or "").strip() or None
		try:
			validate_base_url(self.base_url)
		except ProviderNotConfigured as e:
			frappe.throw(str(e))
		if self.provider_type == "OpenAI-Compatible" and not self.base_url:
			frappe.throw(_("OpenAI-Compatible providers need a Base URL."))
		if not 5 <= cint(self.timeout_seconds or 60) <= 600:
			frappe.throw(_("Timeout must be between 5 and 600 seconds."))
		if not 0 <= cint(self.max_retries) <= 5:
			frappe.throw(_("Max retries must be between 0 and 5."))
		if self.has_value_changed("api_key") or self.has_value_changed("base_url"):
			self.last_test_status = None

	def on_update(self):
		from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit

		changed = [f for f in ("provider_type", "base_url", "default_model", "enabled") if self.has_value_changed(f)]
		if self.has_value_changed("api_key"):
			changed.append("api_key (value not logged)")
		if changed:
			audit("provider_config_changed", "Info", reference_doctype=self.doctype, reference_name=self.name,
				  details={"fields": changed}, durable=False)

	def on_trash(self):
		from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit

		audit("provider_config_deleted", "Warning", details={"provider": self.name}, durable=False)
