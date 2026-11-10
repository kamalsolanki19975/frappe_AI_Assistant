"""Idempotent setup run after every migrate."""

import frappe

from frappe_ai_assistant.frappe_ai_assistant import MANAGER_ROLE, SETTINGS, USER_ROLE

DEFAULT_PROMPTS = [
	("What can you help me with?", "English", None),
	("Why is this order still pending?", "English", "Sales Order"),
	("Which items are still pending from this supplier?", "English", "Purchase Order"),
	("Show me the stock of an item", "English", None),
	("Aaj kitne Sales Orders bane hain?", "Hinglish", None),
	("Is invoice ka payment kyun pending hai?", "Hinglish", "Sales Invoice"),
]


DEFAULT_IMPORT_DOCTYPES = [
	# (DocType, allow_insert, allow_update) — administrators can change this list in Chikku AI Settings
	("Customer", 1, 1), ("Supplier", 1, 1), ("Item", 1, 0), ("Item Price", 1, 1), ("Contact", 1, 0), ("Address", 1, 0),
	("Lead", 1, 0), ("Sales Order", 1, 0), ("Purchase Order", 1, 0), ("Quotation", 1, 0), ("Material Request", 1, 0),
]


def after_migrate():
	ensure_roles()
	ensure_settings()
	ensure_new_setting_defaults()
	ensure_import_doctypes()
	from frappe_ai_assistant.frappe_ai_assistant.knowledge.index import ensure_fulltext_index

	ensure_fulltext_index()


def ensure_roles():
	for role in (USER_ROLE, MANAGER_ROLE):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def ensure_settings():
	saved = frappe.db.sql("select count(*) from `tabSingles` where doctype=%s", SETTINGS)[0][0]
	if saved:
		return
	doc = frappe.get_doc(SETTINGS)
	for df in frappe.get_meta(SETTINGS).fields:
		if df.default is not None and doc.get(df.fieldname) in (None, ""):
			doc.set(df.fieldname, df.default)
	for prompt, lang, dt in DEFAULT_PROMPTS:
		doc.append("suggested_prompts", {"prompt": prompt, "language": lang, "reference_doctype": dt})
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.save()


def ensure_new_setting_defaults():
	"""Fields added in later releases get their default once (existing values are never overwritten)."""
	stored = set(frappe.db.sql_list("select field from `tabSingles` where doctype=%s", SETTINGS))
	changed = False
	for df in frappe.get_meta(SETTINGS).fields:
		if df.default is not None and df.fieldname not in stored and df.fieldtype not in ("Table", "Table MultiSelect"):
			frappe.db.set_single_value(SETTINGS, df.fieldname, df.default)
			changed = True
	if changed:
		frappe.clear_document_cache(SETTINGS, SETTINGS)


def ensure_import_doctypes():
	if frappe.db.count("Chikku AI Import DocType", {"parent": SETTINGS}):
		return
	doc = frappe.get_doc(SETTINGS)
	for dt, ins, upd in DEFAULT_IMPORT_DOCTYPES:
		if frappe.db.exists("DocType", dt):
			doc.append("import_doctypes", {"reference_doctype": dt, "allow_insert": ins, "allow_update": upd})
	doc.flags.ignore_permissions = True
	doc.save()
