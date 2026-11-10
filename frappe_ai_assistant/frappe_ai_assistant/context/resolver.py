"""Turns the browser's Desk context (route, open document, selected rows) into a validated,
permission-checked and size-limited context card. The browser's input is only a hint."""

from __future__ import annotations

import frappe
from frappe.utils import cstr

from frappe_ai_assistant.frappe_ai_assistant.services.erp import query
from frappe_ai_assistant.frappe_ai_assistant.services.erp.policy import PolicyError, check_doctype

MAX_SELECTED = 50
CARD_FIELDS = ["status", "docstatus", "workflow_state", "company", "customer", "customer_name", "supplier",
			   "supplier_name", "party_name", "transaction_date", "posting_date", "delivery_date", "schedule_date",
			   "due_date", "grand_total", "rounded_total", "outstanding_amount", "currency", "per_delivered",
			   "per_billed", "per_received", "item_code", "item_name", "item_group", "stock_uom", "qty",
			   "production_item", "produced_qty", "bom_no", "title"]
CARD_CHILD_FIELDS = ["item_code", "item_name", "qty", "delivered_qty", "received_qty", "billed_amt", "rate",
					 "amount", "warehouse", "uom", "delivery_date", "schedule_date"]
MAX_CARD_CHILD_ROWS = 20


def _doctype_from_slug(slug: str) -> str | None:
	"""/desk/sales-order -> Sales Order (validated against existing DocTypes)."""
	if not slug:
		return None
	name = frappe.db.get_value("DocType", {"name": slug}) or frappe.db.get_value(
		"DocType", {"name": slug.replace("-", " ").title()}
	)
	if name:
		return name
	# Exact match ignoring case for names like "BOM" or "Item Price"
	candidate = slug.replace("-", " ")
	rows = frappe.db.sql("select name from tabDocType where lower(name)=%s limit 1", (candidate.lower(),))
	return rows[0][0] if rows else None


def resolve(raw: dict | None) -> dict:
	"""Returns {"kind": ..., ...} with only validated data, or {} when nothing usable."""
	if not isinstance(raw, dict):
		return {}
	route = raw.get("route")
	route = [cstr(p)[:140] for p in route[:6]] if isinstance(route, list) else []
	doctype = cstr(raw.get("doctype") or "")[:140] or None
	docname = cstr(raw.get("docname") or "")[:140] or None
	kind = cstr(raw.get("kind") or (route[0] if route else "")).lower()

	if not doctype and len(route) >= 2 and kind in ("form", "list", "report"):
		doctype = route[1] if frappe.db.exists("DocType", route[1]) else _doctype_from_slug(route[1])
	if not docname and kind == "form" and len(route) >= 3:
		docname = route[2]

	if not doctype:
		return {"kind": kind or "page", "route": route[:3]} if kind else {}

	try:
		check_doctype(doctype)
	except PolicyError:
		return {"kind": "unavailable"}

	if kind == "form" and docname:
		if docname.startswith("new-"):
			return {"kind": "new_form", "doctype": doctype}
		try:
			doc = query.get_document(doctype, docname, include_children=True, max_child_rows=MAX_CARD_CHILD_ROWS)
		except PolicyError:
			return {"kind": "unavailable"}
		return {"kind": "form", "doctype": doctype, "name": docname, "card": _card(doc)}

	ctx = {"kind": "list" if kind == "list" else kind or "list", "doctype": doctype}
	selected = raw.get("selected")
	if isinstance(selected, list) and selected:
		permitted = []
		for name in selected[:MAX_SELECTED]:
			name = cstr(name)[:140]
			if name and frappe.db.exists(doctype, name) and frappe.has_permission(doctype, "read", name):
				permitted.append(name)
		if permitted:
			ctx["selected"] = permitted
	return ctx


def _card(doc: dict) -> dict:
	card = {"doctype": doc["doctype"], "name": doc["name"], "docstatus": doc.get("docstatus")}
	for f in CARD_FIELDS:
		if f in doc:
			card[f] = doc[f]
	for tf, table in (doc.get("child_tables") or {}).items():
		rows = [{k: v for k, v in r.items() if k in CARD_CHILD_FIELDS} for r in table["rows"]]
		rows = [r for r in rows if r]
		if rows:
			card.setdefault("child_tables", {})[tf] = {
				"row_count": table["row_count"], "rows": rows[:MAX_CARD_CHILD_ROWS],
				"truncated": table["row_count"] > MAX_CARD_CHILD_ROWS,
			}
	return card
