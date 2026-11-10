"""Deterministic, permission-aware business metrics.

Every figure comes from frappe.get_list (role permissions, User Permissions incl. Company and
Warehouse restrictions, sharing, permission query conditions) — never from the model. Each result
carries a trace: source DocTypes, filters, company, period and calculation method.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_first_day, get_last_day, getdate, today

MAX_ROWS = 50000
CHUNK = 400


class MetricError(frappe.ValidationError):
	"""Message is safe to show to the model and the user."""


# ---------------------------------------------------------------- periods
def resolve_period(period: str | None, from_date=None, to_date=None, company: str | None = None) -> dict:
	t = getdate(today())
	p = (period or "this_month").lower().replace(" ", "_")
	if from_date or to_date or p == "custom":
		if not (from_date and to_date):
			raise MetricError("Both from_date and to_date are needed for a custom period.")
		f, e = getdate(from_date), getdate(to_date)
	elif p == "today":
		f = e = t
	elif p == "yesterday":
		f = e = add_days(t, -1)
	elif p == "this_week":
		f, e = add_days(t, -t.weekday()), t
	elif p == "this_month":
		f, e = get_first_day(t), t
	elif p == "last_month":
		f = get_first_day(add_months(t, -1))
		e = get_last_day(f)
	elif p in ("this_quarter", "last_quarter"):
		q_start = dt.date(t.year, 3 * ((t.month - 1) // 3) + 1, 1)
		if p == "this_quarter":
			f, e = q_start, t
		else:
			f = add_months(q_start, -3)
			e = add_days(q_start, -1)
	elif p == "this_year":
		f, e = dt.date(t.year, 1, 1), t
	elif p == "last_year":
		f, e = dt.date(t.year - 1, 1, 1), dt.date(t.year - 1, 12, 31)
	elif p.startswith("last_") and p.endswith("_days"):
		n = cint(p.split("_")[1])
		f, e = add_days(t, -(n - 1)), t
	elif p.startswith("last_") and p.endswith("_months"):
		n = cint(p.split("_")[1])
		f, e = get_first_day(add_months(t, -(n - 1))), t
	elif p in ("this_fiscal_year", "last_fiscal_year"):
		from erpnext.accounts.utils import get_fiscal_year

		ref = t if p == "this_fiscal_year" else add_days(get_fiscal_year(t, company=company)[1], -1)
		fy = get_fiscal_year(ref, company=company)
		f, e = fy[1], min(fy[2], t) if p == "this_fiscal_year" else fy[2]
	else:
		raise MetricError(f"Unknown period '{period}'. Use e.g. this_month, last_month, last_3_months, "
						  "this_quarter, this_fiscal_year or custom with from_date/to_date.")
	f, e = getdate(f), getdate(e)
	if f > e:
		raise MetricError("The period start is after its end.")
	return {"from_date": f, "to_date": e, "label": f"{f.strftime('%d %b %Y')} – {e.strftime('%d %b %Y')}"}


def check_company(company: str | None) -> str:
	if not company:
		company = frappe.defaults.get_user_default("Company")
	if not company:
		raise MetricError("COMPANY_REQUIRED: ask the user which company to report on.")
	if not frappe.db.exists("Company", company):
		raise MetricError("NOT_FOUND_OR_NO_ACCESS: company")
	# ERPNext semantics: company scope comes from User Permissions on Company. If the user has any, the
	# requested company must be one of them. (Document-level permissions are still applied by get_list.)
	from frappe.permissions import get_user_permissions

	allowed = [p.get("doc") for p in get_user_permissions(frappe.session.user).get("Company", [])]
	if allowed and company not in allowed:
		raise MetricError("NOT_FOUND_OR_NO_ACCESS: company")
	return company


def _need(doctype: str):
	if not frappe.has_permission(doctype, "read"):
		raise MetricError(f"NOT_FOUND_OR_NO_ACCESS: you don't have access to {doctype}.")


def _list(doctype, **kw):
	kw.setdefault("limit_page_length", MAX_ROWS)
	return frappe.get_list(doctype, **kw)


def _children(child: str, parent: str, names: list[str], fields: list[str]) -> list[dict]:
	rows = []
	for i in range(0, len(names), CHUNK):
		rows += frappe.get_list(child, parent_doctype=parent, fields=fields,
								filters=[[child, "parent", "in", names[i:i + CHUNK]]],
								limit_page_length=MAX_ROWS)
	return rows


def _result(title, columns, rows, *, kpis=None, chart=None, trace=None, notes=None, refs=None):
	return {"title": title, "columns": columns, "rows": rows, "row_count": len(rows), "kpis": kpis or [],
			"chart": chart, "trace": trace or {}, "notes": notes or [], "refs": refs or []}


def _month(d) -> str:
	return getdate(d).strftime("%Y-%m")


def _top(d: dict, n: int):
	return sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]


# ---------------------------------------------------------------- sales / purchase
def _invoice_summary(kind: str, company, period, group_by, limit, from_date=None, to_date=None):
	dt_, party = ("Sales Invoice", "customer") if kind == "sales" else ("Purchase Invoice", "supplier")
	_need(dt_)
	company = check_company(company)
	per = resolve_period(period, from_date, to_date, company)
	filters = {"docstatus": 1, "company": company, "posting_date": ["between", [per["from_date"], per["to_date"]]]}
	group_by = group_by or "none"
	allowed = {"none", "month", party, "item", "item_group"} | ({"territory", "customer_group", "sales_person"}
																  if kind == "sales" else {"supplier_group"})
	if group_by not in allowed:
		raise MetricError(f"group_by must be one of {sorted(allowed)}")
	invoices = _list(dt_, filters=filters, fields=["name", "posting_date", party, "base_net_total",
													 "base_grand_total", "is_return", "outstanding_amount"]
					 + (["territory", "customer_group"] if kind == "sales" else []))
	net = sum(flt(i.base_net_total) for i in invoices)
	returns = sum(flt(i.base_net_total) for i in invoices if i.is_return)
	kpis = [
		{"label": _("Net {0}").format(_("Sales") if kind == "sales" else _("Purchases")), "value": round(net, 2), "format": "currency"},
		{"label": _("Invoices"), "value": len([i for i in invoices if not i.is_return]), "format": "integer"},
		{"label": _("Returns (net)"), "value": round(returns, 2), "format": "currency"},
		{"label": _("Outstanding on these invoices"), "value": round(sum(flt(i.outstanding_amount) for i in invoices), 2), "format": "currency"},
	]
	agg = defaultdict(float)
	count = defaultdict(int)
	label = group_by
	if group_by in ("item", "item_group"):
		rows = _children(f"{dt_} Item", dt_, [i.name for i in invoices], ["item_code", "item_name", "item_group", "qty", "base_net_amount"])
		qty = defaultdict(float)
		for r in rows:
			k = r.item_code if group_by == "item" else (r.item_group or "—")
			agg[k] += flt(r.base_net_amount)
			qty[k] += flt(r.qty)
			count[k] += 1
		out = [[k, round(qty[k], 3), round(v, 2)] for k, v in _top(agg, limit)]
		columns = [{"key": group_by, "label": _("Item") if group_by == "item" else _("Item Group"), "type": "text"},
				   {"key": "qty", "label": _("Qty"), "type": "number"}, {"key": "net_amount", "label": _("Net Amount"), "type": "currency"}]
		value_key = "net_amount"
	elif group_by == "sales_person":
		rows = _children("Sales Team", dt_, [i.name for i in invoices], ["sales_person", "allocated_amount"])
		for r in rows:
			agg[r.sales_person or "—"] += flt(r.allocated_amount)
			count[r.sales_person or "—"] += 1
		out = [[k, count[k], round(v, 2)] for k, v in _top(agg, limit)]
		columns = [{"key": "sales_person", "label": _("Sales Person"), "type": "text"},
				   {"key": "invoices", "label": _("Invoices"), "type": "integer"},
				   {"key": "allocated", "label": _("Allocated Amount"), "type": "currency"}]
		value_key = "allocated"
	else:
		for i in invoices:
			k = "Total" if group_by == "none" else (_month(i.posting_date) if group_by == "month" else (i.get(group_by) or "—"))
			agg[k] += flt(i.base_net_total)
			count[k] += 1
		items = sorted(agg.items()) if group_by == "month" else _top(agg, limit)
		out = [[k, count[k], round(v, 2)] for k, v in items]
		columns = [{"key": group_by, "label": group_by.replace("_", " ").title(), "type": "text"},
				   {"key": "invoices", "label": _("Invoices"), "type": "integer"},
				   {"key": "net_total", "label": _("Net Total"), "type": "currency"}]
		value_key = "net_total"
	chart = None
	if group_by != "none" and out:
		chart = {"type": "line" if group_by == "month" else "bar", "category": group_by, "values": [value_key],
				 "labels": [str(r[0])[:40] for r in out[:15]], "datasets": [{"name": columns[-1]["label"], "values": [r[-1] for r in out[:15]]}]}
	trace = {"source_doctypes": [dt_] + ([f"{dt_} Item"] if group_by in ("item", "item_group") else []),
			 "filters": {"company": company, "docstatus": "submitted", "posting_date": per["label"]},
			 "method": f"Sum of base net total (company currency) of submitted {dt_}s, returns included as negatives.",
			 "period": per["label"], "company": company, "documents_counted": len(invoices)}
	return _result(f"{'Sales' if kind == 'sales' else 'Purchases'} {per['label']}", columns, out, kpis=kpis,
				   chart=chart, trace=trace, refs=[{"doctype": dt_, "name": i.name} for i in invoices[:5]])


def sales_summary(company=None, period=None, group_by=None, limit=20, from_date=None, to_date=None, **_kw):
	return _invoice_summary("sales", company, period, group_by, cint(limit) or 20, from_date, to_date)


def purchase_summary(company=None, period=None, group_by=None, limit=20, from_date=None, to_date=None, **_kw):
	return _invoice_summary("purchase", company, period, group_by, cint(limit) or 20, from_date, to_date)


def period_comparison(company=None, kind="sales", months=3, **_kw):
	"""Month-by-month comparison of the last N calendar months (current month to date)."""
	dt_ = "Sales Invoice" if kind != "purchase" else "Purchase Invoice"
	_need(dt_)
	company = check_company(company)
	months = max(2, min(cint(months) or 3, 24))
	start = get_first_day(add_months(getdate(today()), -(months - 1)))
	invoices = _list(dt_, filters={"docstatus": 1, "company": company, "posting_date": ["between", [start, today()]]},
					 fields=["posting_date", "base_net_total"])
	buckets = {}
	for m in range(months):
		buckets[_month(add_months(start, m))] = [0.0, 0]
	for i in invoices:
		b = buckets.setdefault(_month(i.posting_date), [0.0, 0])
		b[0] += flt(i.base_net_total)
		b[1] += 1
	rows, prev = [], None
	for k, (v, n) in buckets.items():
		change = None if prev in (None, 0) else round((v - prev) / abs(prev) * 100, 1)
		rows.append([k, n, round(v, 2), change])
		prev = v
	columns = [{"key": "month", "label": _("Month"), "type": "text"}, {"key": "invoices", "label": _("Invoices"), "type": "integer"},
			   {"key": "net_total", "label": _("Net Total"), "type": "currency"},
			   {"key": "change_pct", "label": _("Change vs Previous (%)"), "type": "number"}]
	kpis = [{"label": r[0], "value": r[2], "format": "currency"} for r in rows[-4:]]
	chart = {"type": "line", "category": "month", "values": ["net_total"], "labels": [r[0] for r in rows],
			 "datasets": [{"name": "Net Total", "values": [r[2] for r in rows]}]}
	trace = {"source_doctypes": [dt_], "company": company, "period": f"{start} – {today()}",
			 "filters": {"docstatus": "submitted"}, "method": "Monthly sum of base net total; current month is month-to-date."}
	return _result(f"{'Sales' if dt_ == 'Sales Invoice' else 'Purchases'} by month", columns, rows, kpis=kpis, chart=chart, trace=trace,
				   notes=["The current month is incomplete (month to date)."])


# ---------------------------------------------------------------- receivables / payables
def _ageing(kind, company=None, limit=20, **_kw):
	dt_, party = ("Sales Invoice", "customer") if kind == "receivables" else ("Purchase Invoice", "supplier")
	_need(dt_)
	company = check_company(company)
	inv = _list(dt_, filters={"docstatus": 1, "company": company, "outstanding_amount": ["!=", 0]},
				fields=["name", party, "due_date", "posting_date", "outstanding_amount"])
	t = getdate(today())
	by_party = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
	for i in inv:
		days = (t - getdate(i.due_date or i.posting_date)).days
		b = 0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4
		row = by_party[i.get(party)]
		row[b] += flt(i.outstanding_amount)
		row[5] += flt(i.outstanding_amount)
	ranked = sorted(by_party.items(), key=lambda kv: kv[1][5], reverse=True)[: cint(limit) or 20]
	rows = [[k, *[round(x, 2) for x in v]] for k, v in ranked]
	total = sum(v[5] for v in by_party.values())
	overdue = sum(sum(v[1:5]) for v in by_party.values())
	columns = [{"key": party, "label": party.title(), "type": "text"},
			   *[{"key": k, "label": l, "type": "currency"} for k, l in
				 [("not_due", _("Not Due")), ("d0_30", "1-30"), ("d31_60", "31-60"), ("d61_90", "61-90"), ("d90", "90+"), ("total", _("Total"))]]]
	kpis = [{"label": _("Total Outstanding"), "value": round(total, 2), "format": "currency"},
			{"label": _("Overdue"), "value": round(overdue, 2), "format": "currency"},
			{"label": _("Open Invoices"), "value": len(inv), "format": "integer"}]
	chart = {"type": "bar", "category": party, "values": ["total"], "labels": [str(r[0])[:40] for r in rows[:12]],
			 "datasets": [{"name": "Outstanding", "values": [r[-1] for r in rows[:12]]}]}
	trace = {"source_doctypes": [dt_], "company": company, "as_of": str(t),
			 "method": f"Outstanding amount of submitted {dt_}s bucketed by days past due date. Unallocated advances and "
					   "journal entries are not included; use the SaarthiERP Accounts Receivable/Payable report for the ledger view."}
	return _result(f"{kind.title()} as of {t}", columns, rows, kpis=kpis, chart=chart, trace=trace)


def receivables(**kw):
	return _ageing("receivables", **kw)


def payables(**kw):
	return _ageing("payables", **kw)


# ---------------------------------------------------------------- inventory
def _company_warehouses(company, warehouse=None):
	_need("Warehouse")
	filters = {"company": company, "is_group": 0}
	if warehouse:
		filters["name"] = warehouse
	return _list("Warehouse", filters=filters, pluck="name")


def stock_balance(company=None, warehouse=None, group_by="warehouse", limit=20, **_kw):
	_need("Bin")
	company = check_company(company)
	whs = _company_warehouses(company, warehouse)
	if not whs:
		return _result("Stock balance", [], [], notes=["No accessible warehouses for this company."])
	bins = _list("Bin", filters={"warehouse": ["in", whs], "actual_qty": ["!=", 0]},
				 fields=["item_code", "warehouse", "actual_qty", "stock_value", "projected_qty"])
	key = "warehouse" if group_by != "item" else "item_code"
	agg, qty = defaultdict(float), defaultdict(float)
	for b in bins:
		agg[b.get(key)] += flt(b.stock_value)
		qty[b.get(key)] += flt(b.actual_qty)
	rows = [[k, round(qty[k], 3), round(v, 2)] for k, v in _top(agg, cint(limit) or 20)]
	total = sum(flt(b.stock_value) for b in bins)
	columns = [{"key": key, "label": key.replace("_", " ").title(), "type": "text"},
			   {"key": "qty", "label": _("Qty"), "type": "number"}, {"key": "stock_value", "label": _("Stock Value"), "type": "currency"}]
	kpis = [{"label": _("Inventory Value"), "value": round(total, 2), "format": "currency"},
			{"label": _("Item-Warehouse Lines"), "value": len(bins), "format": "integer"}]
	chart = {"type": "pie" if key == "warehouse" else "bar", "category": key, "values": ["stock_value"],
			 "labels": [str(r[0])[:40] for r in rows[:10]], "datasets": [{"name": "Stock Value", "values": [r[2] for r in rows[:10]]}]}
	trace = {"source_doctypes": ["Bin", "Warehouse"], "company": company,
			 "method": "Current Bin actual quantity and stock value (valuation) for the company's warehouses you can access."}
	return _result("Inventory valuation", columns, rows, kpis=kpis, chart=chart, trace=trace)


def slow_moving(company=None, days=90, limit=30, **_kw):
	_need("Bin")
	_need("Stock Ledger Entry")
	company = check_company(company)
	days = max(7, min(cint(days) or 90, 730))
	whs = _company_warehouses(company)
	bins = _list("Bin", filters={"warehouse": ["in", whs], "actual_qty": [">", 0]},
				 fields=["item_code", "warehouse", "actual_qty", "stock_value"]) if whs else []
	cutoff = add_days(today(), -days)
	moved = set(_list("Stock Ledger Entry", filters=[["actual_qty", "<", 0], ["is_cancelled", "=", 0],
													  ["posting_date", ">=", cutoff], ["company", "=", company]],
					  fields=["item_code"], group_by="item_code", pluck="item_code"))
	agg = defaultdict(lambda: [0.0, 0.0])
	for b in bins:
		if b.item_code not in moved:
			agg[b.item_code][0] += flt(b.actual_qty)
			agg[b.item_code][1] += flt(b.stock_value)
	ranked = sorted(agg.items(), key=lambda kv: kv[1][1], reverse=True)[: cint(limit) or 30]
	last_out = {}
	if ranked:
		for r in _list("Stock Ledger Entry", filters=[["item_code", "in", [k for k, _v in ranked]], ["actual_qty", "<", 0],
													  ["is_cancelled", "=", 0], ["company", "=", company]],
					   fields=["item_code", {"MAX": "posting_date", "as": "last_out"}], group_by="item_code"):
			last_out[r.item_code] = str(r.last_out)
	rows = [[k, round(v[0], 3), round(v[1], 2), last_out.get(k, "never")] for k, v in ranked]
	columns = [{"key": "item_code", "label": _("Item"), "type": "text"}, {"key": "qty", "label": _("Qty in Stock"), "type": "number"},
			   {"key": "stock_value", "label": _("Stock Value"), "type": "currency"}, {"key": "last_out", "label": _("Last Outward"), "type": "text"}]
	kpis = [{"label": _("Slow-moving Items"), "value": len(agg), "format": "integer"},
			{"label": _("Value Tied Up"), "value": round(sum(v[1] for v in agg.values()), 2), "format": "currency"}]
	trace = {"source_doctypes": ["Bin", "Stock Ledger Entry"], "company": company,
			 "method": f"Items with stock on hand and no outward stock movement in the last {days} days (since {cutoff})."}
	return _result(f"Slow-moving stock (no outward movement in {days} days)", columns, rows, kpis=kpis, trace=trace,
				   chart={"type": "bar", "category": "item_code", "values": ["stock_value"], "labels": [r[0] for r in rows[:12]],
						  "datasets": [{"name": "Stock Value", "values": [r[2] for r in rows[:12]]}]} if rows else None)


def stock_shortages(company=None, limit=30, **_kw):
	_need("Bin")
	company = check_company(company)
	whs = _company_warehouses(company)
	bins = _list("Bin", filters={"warehouse": ["in", whs], "projected_qty": ["<", 0]},
				 fields=["item_code", "warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"],
				 order_by="projected_qty asc", limit_page_length=cint(limit) or 30) if whs else []
	reorder = []
	if frappe.has_permission("Item", "read"):
		levels = _list("Item Reorder", parent_doctype="Item", filters=[["Item Reorder", "warehouse", "in", whs]] if whs else [],
					   fields=["parent", "warehouse", "warehouse_reorder_level", "warehouse_reorder_qty"])
		if levels:
			qty = {(b.item_code, b.warehouse): flt(b.projected_qty) for b in _list(
				"Bin", filters={"item_code": ["in", list({l.parent for l in levels})]}, fields=["item_code", "warehouse", "projected_qty"])}
			reorder = [[l.parent, l.warehouse, qty.get((l.parent, l.warehouse), 0), l.warehouse_reorder_level, l.warehouse_reorder_qty]
					   for l in levels if qty.get((l.parent, l.warehouse), 0) <= flt(l.warehouse_reorder_level)]
	rows = [[b.item_code, b.warehouse, b.actual_qty, b.reserved_qty, b.ordered_qty, b.projected_qty] for b in bins]
	columns = [{"key": k, "label": l, "type": t} for k, l, t in [
		("item_code", _("Item"), "text"), ("warehouse", _("Warehouse"), "text"), ("actual_qty", _("Actual"), "number"),
		("reserved_qty", _("Reserved"), "number"), ("ordered_qty", _("Ordered"), "number"), ("projected_qty", _("Projected"), "number")]]
	kpis = [{"label": _("Short Item-Warehouses"), "value": len(rows), "format": "integer"},
			{"label": _("Below Reorder Level"), "value": len(reorder), "format": "integer"}]
	trace = {"source_doctypes": ["Bin", "Item Reorder"], "company": company,
			 "method": "Bins whose projected quantity (actual + ordered + planned - reserved) is below zero, and items at or "
					   "below their configured warehouse reorder level. This shows current shortages, not historical frequency."}
	res = _result("Stock shortages", columns, rows, kpis=kpis, trace=trace)
	res["reorder_rows"] = reorder[:50]
	return res


# ---------------------------------------------------------------- delays
def delayed_sales_orders(company=None, min_days=0, limit=50, **_kw):
	_need("Sales Order")
	company = check_company(company)
	cutoff = add_days(today(), -cint(min_days))
	so = _list("Sales Order", filters={"docstatus": 1, "company": company, "delivery_date": ["<", cutoff],
										"status": ["in", ["To Deliver and Bill", "To Deliver"]], "per_delivered": ["<", 100]},
			   fields=["name", "customer", "delivery_date", "per_delivered", "base_grand_total"], order_by="delivery_date asc",
			   limit_page_length=cint(limit) or 50)
	t = getdate(today())
	rows = [[s.name, s.customer, str(s.delivery_date), (t - getdate(s.delivery_date)).days, flt(s.per_delivered), flt(s.base_grand_total)] for s in so]
	columns = [{"key": k, "label": l, "type": ty} for k, l, ty in [
		("name", _("Sales Order"), "text"), ("customer", _("Customer"), "text"), ("delivery_date", _("Delivery Date"), "text"),
		("days_late", _("Days Late"), "integer"), ("per_delivered", _("% Delivered"), "number"), ("value", _("Order Value"), "currency")]]
	trace = {"source_doctypes": ["Sales Order"], "company": company,
			 "method": "Submitted Sales Orders (To Deliver / To Deliver and Bill) whose delivery date has passed and that are not fully delivered."}
	return _result("Delayed Sales Orders", columns, rows, kpis=[{"label": _("Delayed Orders"), "value": len(rows), "format": "integer"}],
				   trace=trace, refs=[{"doctype": "Sales Order", "name": s.name} for s in so[:8]])


def delayed_purchase_orders(company=None, min_days=0, limit=50, **_kw):
	_need("Purchase Order")
	company = check_company(company)
	cutoff = add_days(today(), -cint(min_days))
	po = _list("Purchase Order", filters={"docstatus": 1, "company": company, "schedule_date": ["<", cutoff],
										   "per_received": ["<", 100], "status": ["not in", ["Closed", "Completed", "On Hold"]]},
			   fields=["name", "supplier", "schedule_date", "per_received", "base_grand_total"], order_by="schedule_date asc",
			   limit_page_length=cint(limit) or 50)
	t = getdate(today())
	rows = [[p.name, p.supplier, str(p.schedule_date), (t - getdate(p.schedule_date)).days, flt(p.per_received), flt(p.base_grand_total)] for p in po]
	columns = [{"key": k, "label": l, "type": ty} for k, l, ty in [
		("name", _("Purchase Order"), "text"), ("supplier", _("Supplier"), "text"), ("schedule_date", _("Required By"), "text"),
		("days_late", _("Days Late"), "integer"), ("per_received", _("% Received"), "number"), ("value", _("Order Value"), "currency")]]
	trace = {"source_doctypes": ["Purchase Order"], "company": company,
			 "method": "Submitted Purchase Orders past their required-by date and not fully received (excluding closed/on hold)."}
	return _result("Delayed purchase deliveries", columns, rows, kpis=[{"label": _("Delayed POs"), "value": len(rows), "format": "integer"}],
				   trace=trace, refs=[{"doctype": "Purchase Order", "name": p.name} for p in po[:8]])


# ---------------------------------------------------------------- manufacturing
def manufacturing(company=None, period="this_month", from_date=None, to_date=None, **_kw):
	_need("Work Order")
	company = check_company(company)
	per = resolve_period(period, from_date, to_date, company)
	wos = _list("Work Order", filters={"docstatus": 1, "company": company,
									   "planned_start_date": ["between", [per["from_date"], f"{per['to_date']} 23:59:59"]]},
				fields=["name", "production_item", "status", "qty", "produced_qty", "planned_end_date"])
	by_status = defaultdict(int)
	for w in wos:
		by_status[w.status] += 1
	planned = sum(flt(w.qty) for w in wos)
	produced = sum(flt(w.produced_qty) for w in wos)
	now = frappe.utils.now_datetime()
	delayed = [w for w in wos if w.status not in ("Completed", "Stopped", "Closed", "Cancelled") and w.planned_end_date and w.planned_end_date < now]
	consumption = []
	if wos:
		items = _children("Work Order Item", "Work Order", [w.name for w in wos], ["item_code", "required_qty", "transferred_qty", "consumed_qty"])
		agg = defaultdict(lambda: [0.0, 0.0, 0.0])
		for i in items:
			a = agg[i.item_code]
			a[0] += flt(i.required_qty)
			a[1] += flt(i.transferred_qty)
			a[2] += flt(i.consumed_qty)
		consumption = [[k, round(v[0], 3), round(v[1], 3), round(v[2], 3)] for k, v in sorted(agg.items(), key=lambda kv: kv[1][0], reverse=True)[:25]]
	rows = [[s, n] for s, n in sorted(by_status.items(), key=lambda kv: -kv[1])]
	columns = [{"key": "status", "label": _("Status"), "type": "text"}, {"key": "count", "label": _("Work Orders"), "type": "integer"}]
	kpis = [{"label": _("Work Orders"), "value": len(wos), "format": "integer"},
			{"label": _("Completion"), "value": round(produced / planned * 100, 1) if planned else 0, "format": "number"},
			{"label": _("Delayed"), "value": len(delayed), "format": "integer"},
			{"label": _("Produced Qty"), "value": round(produced, 2), "format": "number"}]
	trace = {"source_doctypes": ["Work Order", "Work Order Item"], "company": company, "period": per["label"],
			 "method": "Submitted Work Orders planned to start in the period; completion = produced qty / planned qty; delayed = "
					   "planned end passed and not completed/stopped. Consumption from Work Order required/transferred/consumed qty."}
	res = _result(f"Manufacturing {per['label']}", columns, rows, kpis=kpis, trace=trace,
				  chart={"type": "pie", "category": "status", "values": ["count"], "labels": [r[0] for r in rows],
						 "datasets": [{"name": "Work Orders", "values": [r[1] for r in rows]}]} if rows else None,
				  refs=[{"doctype": "Work Order", "name": w.name} for w in delayed[:8]])
	res["delayed"] = [[w.name, w.production_item, str(w.planned_end_date), w.status] for w in delayed[:30]]
	res["consumption"] = consumption
	return res


METRICS = {
	"sales_summary": (sales_summary, "Sales (net, submitted invoices) for a period; group_by none/month/customer/item/item_group/territory/customer_group/sales_person."),
	"purchase_summary": (purchase_summary, "Purchases for a period; group_by none/month/supplier/item/item_group/supplier_group."),
	"period_comparison": (period_comparison, "Month-by-month comparison of sales (kind=sales) or purchases (kind=purchase) for the last N months."),
	"receivables": (receivables, "Customer outstanding with ageing buckets."),
	"payables": (payables, "Supplier outstanding with ageing buckets."),
	"stock_balance": (stock_balance, "Inventory quantity and valuation by warehouse or item."),
	"slow_moving": (slow_moving, "Items in stock with no outward movement for N days."),
	"stock_shortages": (stock_shortages, "Negative projected stock and items below reorder level."),
	"delayed_sales_orders": (delayed_sales_orders, "Sales Orders past delivery date and not fully delivered."),
	"delayed_purchase_orders": (delayed_purchase_orders, "Purchase Orders past required date and not fully received."),
	"manufacturing": (manufacturing, "Work Order status, completion, delays and material consumption for a period."),
}


def run(metric: str, params: dict) -> dict:
	if metric not in METRICS:
		raise MetricError(f"Unknown metric. Available: {', '.join(METRICS)}")
	return METRICS[metric][0](**(params or {}))
