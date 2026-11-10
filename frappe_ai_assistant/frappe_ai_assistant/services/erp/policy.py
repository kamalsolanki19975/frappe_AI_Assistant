"""Chikku AI's own DocType policy, applied on top of Frappe permissions.

Frappe permissions decide what the *user* may see. This module additionally keeps
configuration, security and system DocTypes out of the assistant entirely.
"""

import frappe

DENY_DOCTYPES = {
	# identity, permissions, security
	"User", "Role", "Role Profile", "Has Role", "DocPerm", "Custom DocPerm", "User Permission",
	"User Group", "Module Profile", "Access Control", "OAuth Client", "OAuth Bearer Token",
	"OAuth Authorization Code", "Social Login Key", "Token Cache", "Connected App", "LDAP Settings",
	# code and automation
	"Server Script", "Client Script", "Custom Field", "Property Setter", "DocType", "Customize Form",
	"Workflow", "Workflow State", "Workflow Action", "Webhook", "Scheduled Job Type", "Scheduled Job Log",
	"Notification", "Assignment Rule", "Auto Repeat", "Report", "Print Format", "Web Form", "Page",
	"Data Import", "Data Export", "Package", "Module Def",
	# integrations, mail, logs
	"Email Account", "Email Queue", "Email Domain", "Integration Request", "Error Log", "Activity Log",
	"Access Log", "Version", "Deleted Document", "Route History", "Communication", "Comment", "File",
	"Session Default Settings", "Prepared Report", "Console Log", "RQ Job", "RQ Worker",
	# ledgers (summaries should go through reports, not raw rows)
	"GL Entry", "Stock Ledger Entry", "Payment Ledger Entry", "Serial and Batch Entry",
}
DENY_PREFIXES = ("Chikku AI ",)

# Field types never sent to the model.
HIDDEN_FIELDTYPES = {"Password", "Code", "HTML Editor", "Attach", "Attach Image", "Signature", "Geolocation",
					 "Button", "HTML", "Image", "Fold", "Heading", "Section Break", "Column Break", "Tab Break",
					 "Table", "Table MultiSelect", "JSON"}
NUMERIC_FIELDTYPES = {"Currency", "Float", "Int", "Percent"}
STANDARD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}


class PolicyError(frappe.ValidationError):
	"""Message is safe to show to the model."""


def check_doctype(doctype) -> "frappe.model.meta.Meta":
	if not isinstance(doctype, str) or not doctype or len(doctype) > 140:
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	if doctype in DENY_DOCTYPES or doctype.startswith(DENY_PREFIXES):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	if not frappe.db.exists("DocType", doctype):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	meta = frappe.get_meta(doctype)
	if meta.issingle or meta.istable or getattr(meta, "is_virtual", 0):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	if not frappe.has_permission(doctype, "read"):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	return meta


def readable_fields(meta) -> dict:
	"""fieldname -> docfield for fields the session user may read and the model may see."""
	permitted = set(meta.get_permitted_fieldnames(user=frappe.session.user))
	out = {}
	for df in meta.fields:
		if df.fieldtype in HIDDEN_FIELDTYPES or df.fieldname not in permitted:
			continue
		if df.fieldtype == "Data" and (df.options or "") in ("Password",):
			continue
		out[df.fieldname] = df
	return out
