# Copyright (c) 2026, Simpel and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from frappe_ai_assistant.frappe_ai_assistant.doctype.private_document import PrivateDocument


class ChikkuAIAlertSubscription(PrivateDocument):
	def before_insert(self):
		self.user = frappe.session.user  # subscriptions are always for the person creating them

	def validate(self):
		if not self.is_new() and self.has_value_changed("user"):
			frappe.throw(_("A subscription cannot be moved to another user."))
		if self.company and not frappe.has_permission("Company", "read", self.company):
			frappe.throw(_("You don't have access to this company."))
		if self.has_value_changed("rule") or self.has_value_changed("company") or self.has_value_changed("threshold_days"):
			self.last_signature = None
