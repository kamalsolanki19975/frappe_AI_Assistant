"""Base controller for Chikku AI records.

Frappe's default Document.notify_update() broadcasts a site-wide `list_update` event
(document name + modifying user) to every connected Desk user. For private assistant data
that is a metadata leak, so Chikku records only publish `doc_update`, which Frappe sends to
the document's own room; joining that room requires read permission on the document.
"""

import frappe
from frappe.model.document import Document


class PrivateDocument(Document):
	def notify_update(self):
		if frappe.flags.in_import or frappe.flags.in_patch or frappe.flags.in_migrate or frappe.flags.in_install:
			return
		frappe.publish_realtime(
			"doc_update",
			{"modified": self.modified, "doctype": self.doctype, "name": self.name},
			doctype=self.doctype,
			docname=self.name,
			after_commit=True,
		)
