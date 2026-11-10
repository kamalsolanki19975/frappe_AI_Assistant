# Copyright (c) 2026, Simpel and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

LIMITS = {
	"context_message_limit": (0, 100),
	"max_context_chars": (2000, 400000),
	"max_response_tokens": (64, 32000),
	"timeout_seconds": (10, 600),
	"max_retries": (0, 5),
	"max_tool_calls_per_turn": (0, 20),
	"max_upload_mb": (1, 50),
	"backdate_days": (0, 365),
	"action_expiry_minutes": (1, 60),
}


class ChikkuAISettings(Document):
	def validate(self):
		for field, (lo, hi) in LIMITS.items():
			value = cint(self.get(field))
			if value and not lo <= value <= hi:
				frappe.throw(_("{0} must be between {1} and {2}.").format(self.meta.get_label(field), lo, hi))
		if not 0 <= flt(self.temperature) <= 2:
			frappe.throw(_("Temperature must be between 0 and 2."))
		for slot in ("reasoning", "fallback", "fast", "vision", "embedding", "stt", "tts"):
			if self.get(f"{slot}_model") and not self.get(f"{slot}_provider"):
				frappe.throw(_("Select a provider for the {0} model.").format(slot))
		if self.fallback_provider and self.fallback_provider == self.reasoning_provider and (
			(self.fallback_model or "") == (self.reasoning_model or "")
		):
			frappe.throw(_("The fallback model must differ from the main model."))

	def on_update(self):
		from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit

		changed = [f for f in ("enabled", "reasoning_provider", "reasoning_model", "fallback_provider",
							   "draft_actions_enabled", "erp_read_tools_enabled", "system_instructions",
							   "backdate_days", "monthly_cost_limit") if self.has_value_changed(f)]
		if changed:
			audit("settings_changed", "Info", details={"fields": changed}, durable=False)
