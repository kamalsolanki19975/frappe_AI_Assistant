# Copyright (c) 2026, Simpel and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe_ai_assistant.frappe_ai_assistant.doctype.private_document import PrivateDocument


class ChikkuAIConversation(PrivateDocument):
	def before_insert(self):
		# The owner is always the session user; a client-supplied user is ignored.
		self.user = frappe.session.user

	def validate(self):
		if not self.is_new() and self.has_value_changed("user"):
			frappe.throw(_("A conversation cannot be moved to another user."))
