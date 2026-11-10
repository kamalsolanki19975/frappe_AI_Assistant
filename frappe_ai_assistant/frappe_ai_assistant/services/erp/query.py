"""Permission-aware ERP reads.

Every read goes through frappe.get_list (role permissions, User Permissions, sharing,
permission_query_conditions, if_owner) or Document.check_permission +
apply_fieldlevel_read_permissions. Nothing here uses get_all, raw SQL or ignore_permissions.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import frappe
from frappe.utils import cint, cstr

from .policy import (
	NUMERIC_FIELDTYPES,
	STANDARD_FIELDS,
	PolicyError,
	check_doctype,
	readable_fields,
)

OPERATORS = {"=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "between", "is"}
MAX_LIMIT = 50
MAX_FILTERS = 10
MAX_TEXT = 500
MAX_CHILD_ROWS = 50


def _jsonable(value):
	if isinstance(value, (dt.date, dt.datetime, dt.time)):
		return value.isoformat()
	if isinstance(value, dt.timedelta):
		return str(value)
	if isinstance(value, Decimal):
		return float(value)
	if isinstance(value, str) and len(value) > MAX_TEXT:
		return value[:MAX_TEXT] + "…"
	return value


def clean_row(row: dict) -> dict:
	return {k: _jsonable(v) for k, v in row.items()}


def _scalar(v):
	if v is None or isinstance(v, (str, int, float, bool)):
		if isinstance(v, str) and len(v) > 200:
			raise PolicyError("INVALID_ARGS: filter value too long")
		return v
	raise PolicyError("INVALID_ARGS: filter values must be text, numbers or booleans")


def validate_filters(meta, filters, fields: dict) -> list[list]:
	if not filters:
		return []
	if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
		raise PolicyError(f"INVALID_ARGS: filters must be a list of at most {MAX_FILTERS} [field, operator, value]")
	out = []
	for f in filters:
		if not isinstance(f, (list, tuple)) or len(f) != 3:
			raise PolicyError("INVALID_ARGS: each filter must be [field, operator, value]")
		field, op, value = f
		op = cstr(op).lower().strip()
		if field not in fields and field not in STANDARD_FIELDS:
			raise PolicyError(f"INVALID_ARGS: unknown or unreadable field '{cstr(field)[:60]}'")
		if op not in OPERATORS:
			raise PolicyError(f"INVALID_ARGS: operator '{op[:20]}' is not allowed")
		if op in ("in", "not in", "between"):
			if not isinstance(value, list) or not 0 < len(value) <= 100:
				raise PolicyError("INVALID_ARGS: 'in'/'between' need a list of values")
			value = [_scalar(v) for v in value]
			if op == "between" and len(value) != 2:
				raise PolicyError("INVALID_ARGS: 'between' needs exactly two values")
		elif op == "is":
			if value not in ("set", "not set"):
				raise PolicyError("INVALID_ARGS: 'is' accepts 'set' or 'not set'")
		else:
			value = _scalar(value)
		out.append([meta.name, field, op, value])
	return out


def default_list_fields(meta, fields: dict) -> list[str]:
	chosen = ["name"]
	title = meta.get_title_field()
	for fn in [title, "status", "docstatus", "company", "customer", "supplier", "posting_date",
			   "transaction_date", "grand_total", "item_name", "item_group", "customer_name", "supplier_name",
			   "disabled", "workflow_state"]:
		if fn and fn not in chosen and (fn in fields or fn in STANDARD_FIELDS):
			chosen.append(fn)
	for fn in meta.get_list_view_fields() if hasattr(meta, "get_list_view_fields") else []:
		if fn in fields and fn not in chosen:
			chosen.append(fn)
	return chosen[:12]


def search(doctype: str, *, filters=None, search_text: str | None = None, fields=None, order_by=None,
		   limit: int = 20) -> dict:
	meta = check_doctype(doctype)
	readable = readable_fields(meta)
	if fields:
		if not isinstance(fields, list) or len(fields) > 15:
			raise PolicyError("INVALID_ARGS: fields must be a list of at most 15 field names")
		bad = [f for f in fields if f not in readable and f not in STANDARD_FIELDS]
		if bad:
			raise PolicyError(f"INVALID_ARGS: unknown or unreadable fields {bad[:5]}")
		select = list(dict.fromkeys(["name", *fields]))
	else:
		select = default_list_fields(meta, readable)
	flt = validate_filters(meta, filters, readable)

	or_filters = []
	if search_text:
		text = cstr(search_text).strip()[:100]
		like = f"%{text}%"
		candidates = ["name", meta.get_title_field(), *(meta.get_search_fields() or [])]
		for fn in dict.fromkeys(candidates):
			if fn and (fn == "name" or fn in readable):
				or_filters.append([doctype, fn, "like", like])

	order = "modified desc"
	if order_by:
		parts = cstr(order_by).strip().split()
		if len(parts) not in (1, 2) or (parts[0] not in readable and parts[0] not in STANDARD_FIELDS) or (
			len(parts) == 2 and parts[1].lower() not in ("asc", "desc")
		):
			raise PolicyError("INVALID_ARGS: order_by must be '<field> asc' or '<field> desc'")
		order = " ".join(parts)

	limit = max(1, min(cint(limit) or 20, MAX_LIMIT))
	rows = frappe.get_list(doctype, fields=select, filters=flt, or_filters=or_filters or None,
						   order_by=order, limit_page_length=limit + 1)
	truncated = len(rows) > limit
	rows = [clean_row(r) for r in rows[:limit]]
	title_field = meta.get_title_field()
	return {
		"doctype": doctype,
		"rows": rows,
		"row_count": len(rows),
		"truncated": truncated,
		"refs": [{"doctype": doctype, "name": r["name"], "title": r.get(title_field) or r["name"]} for r in rows],
	}


def get_document(doctype: str, name: str, *, include_children: bool = True,
				 max_child_rows: int = MAX_CHILD_ROWS) -> dict:
	meta = check_doctype(doctype)
	if not isinstance(name, str) or not name or len(name) > 140:
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	if not frappe.db.exists(doctype, name):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	doc = frappe.get_doc(doctype, name)
	if not doc.has_permission("read"):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	doc.apply_fieldlevel_read_permissions()
	readable = readable_fields(meta)
	data = {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}
	for fn in readable:
		value = doc.get(fn)
		if value not in (None, "", 0) or fn in ("status",):
			data[fn] = _jsonable(value)
	children = {}
	if include_children:
		max_child_rows = max(0, min(cint(max_child_rows), MAX_CHILD_ROWS))
		for tdf in meta.get_table_fields():
			if tdf.fieldname not in meta.get_permitted_fieldnames(user=frappe.session.user):
				continue
			cmeta = frappe.get_meta(tdf.options)
			cfields = [df.fieldname for df in cmeta.fields
					   if df.fieldtype not in {"Section Break", "Column Break", "Tab Break", "HTML", "Button",
											   "Password", "Code", "Attach", "Attach Image", "Table"}
					   and df.fieldname in set(cmeta.get_permitted_fieldnames(parenttype=doctype,
																			  user=frappe.session.user))]
			rows = doc.get(tdf.fieldname) or []
			if not rows:
				continue
			children[tdf.fieldname] = {
				"row_count": len(rows),
				"rows": [clean_row({f: r.get(f) for f in cfields if r.get(f) not in (None, "")})
						 for r in rows[:max_child_rows]],
				"truncated": len(rows) > max_child_rows,
			}
	if children:
		data["child_tables"] = children
	return data


def aggregate(doctype: str, *, filters=None, group_by: str | None = None, metric: str = "count",
			  value_field: str | None = None, limit: int = 20) -> dict:
	meta = check_doctype(doctype)
	readable = readable_fields(meta)
	flt = validate_filters(meta, filters, readable)
	if meta.has_field("company") and "company" in readable:
		if not any(f[1] == "company" and f[2] in ("=", "in") for f in flt):
			raise PolicyError(
				"COMPANY_REQUIRED: this DocType has a company field. Ask the user which company "
				"to summarise and pass a filter ['company', '=', '<company>']."
			)
	metric = cstr(metric).lower()
	if metric not in ("count", "sum", "avg", "min", "max"):
		raise PolicyError("INVALID_ARGS: metric must be count, sum, avg, min or max")
	fields = []
	if metric == "count":
		fields.append({"COUNT": "*", "as": "value"})
	else:
		if value_field not in readable or readable[value_field].fieldtype not in NUMERIC_FIELDTYPES:
			raise PolicyError("INVALID_ARGS: value_field must be a readable numeric field")
		fields.append({metric.upper(): value_field, "as": "value"})
	if group_by:
		if group_by not in readable or readable[group_by].fieldtype in NUMERIC_FIELDTYPES:
			raise PolicyError("INVALID_ARGS: group_by must be a readable, non-numeric field")
		fields.insert(0, group_by)
	limit = max(1, min(cint(limit) or 20, MAX_LIMIT))
	rows = frappe.get_list(doctype, fields=fields, filters=flt, group_by=group_by or None,
						   order_by="value desc" if group_by else None, limit_page_length=limit)
	return {
		"doctype": doctype,
		"metric": metric,
		"value_field": value_field,
		"group_by": group_by,
		"filters": [f[1:] for f in flt],
		"rows": [clean_row(r) for r in rows],
		"note": "Includes only records you are permitted to see. Draft and cancelled documents are "
		"included unless a docstatus filter is given (1 = submitted).",
	}


def item_stock(item_code: str, warehouse: str | None = None) -> dict:
	if not isinstance(item_code, str) or not frappe.db.exists("Item", item_code):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	if not frappe.has_permission("Item", "read", item_code):
		raise PolicyError("NOT_FOUND_OR_NO_ACCESS")
	check_doctype("Bin")
	filters = [["Bin", "item_code", "=", item_code]]
	if warehouse:
		filters.append(["Bin", "warehouse", "=", cstr(warehouse)[:140]])
	rows = frappe.get_list(
		"Bin", filters=filters,
		fields=["warehouse", "actual_qty", "reserved_qty", "ordered_qty", "indented_qty", "projected_qty",
				"stock_uom"],
		order_by="actual_qty desc", limit_page_length=MAX_LIMIT,
	)
	return {
		"item_code": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name"),
		"warehouses": [clean_row(r) for r in rows],
		"total_actual_qty": sum(r.actual_qty or 0 for r in rows),
		"total_projected_qty": sum(r.projected_qty or 0 for r in rows),
		"refs": [{"doctype": "Item", "name": item_code, "title": item_code}],
	}
