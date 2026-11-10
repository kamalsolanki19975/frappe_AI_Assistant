"""Draft document builders. Each action has:
- validate(params) -> normalised params + preview (no writes),
- execute(params) -> inserts a docstatus=0 document as the session user.

Validation runs twice: when the preview is prepared and again right before execution.
Nothing here submits, cancels, deletes or applies workflow actions.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr, flt, getdate, today

from .business_rules import BusinessRuleError, check_backdate


class DraftValidationError(frappe.ValidationError):
	pass


def _require(cond, msg):
	if not cond:
		raise DraftValidationError(msg)


def _readable_link(doctype: str, name, label: str):
	_require(isinstance(name, str) and name.strip(), _("{0} is required.").format(label))
	name = name.strip()
	_require(frappe.db.exists(doctype, name) and frappe.has_permission(doctype, "read", name),
			 _("{0} '{1}' was not found or you can't access it.").format(label, name[:80]))
	return name


# ---------------------------------------------------------------- Sales Order
SALES_ORDER_ACTION = "draft.sales_order.create"


def _company_link(doctype: str, name, company: str, label: str):
	name = _readable_link(doctype, name, label)
	_require(frappe.db.get_value(doctype, name, "company") == company,
			 _("{0} {1} belongs to another company.").format(label, name))
	return name


def validate_sales_order(settings, params: dict) -> tuple[dict, dict]:
	_require(frappe.has_permission("Sales Order", "create"), _("You are not allowed to create Sales Orders."))
	customer = _readable_link("Customer", params.get("customer"), _("Customer"))
	_require(not frappe.db.get_value("Customer", customer, "disabled"), _("Customer is disabled."))
	company = _readable_link("Company", params.get("company"), _("Company"))

	transaction_date = getdate(params.get("transaction_date") or today())
	delivery_date = params.get("delivery_date")
	_require(delivery_date, _("Delivery date is required."))
	delivery_date = getdate(delivery_date)
	_require(delivery_date >= transaction_date, _("Delivery date cannot be before the order date."))
	try:
		check_backdate(settings, transaction_date, _("Order date"))
	except BusinessRuleError as e:
		raise DraftValidationError(str(e))

	set_warehouse = None
	if params.get("set_warehouse"):
		set_warehouse = _company_link("Warehouse", params.get("set_warehouse"), company, _("Warehouse"))
	taxes_template = None
	if params.get("taxes_and_charges"):
		taxes_template = _company_link("Sales Taxes and Charges Template", params.get("taxes_and_charges"), company,
									   _("Tax template"))
		_require(not frappe.db.get_value("Sales Taxes and Charges Template", taxes_template, "disabled"),
				 _("Tax template {0} is disabled.").format(taxes_template))

	items = params.get("items") or []
	_require(isinstance(items, list) and 0 < len(items) <= 50, _("Add between 1 and 50 items."))
	clean_items = []
	for i, row in enumerate(items, start=1):
		_require(isinstance(row, dict), _("Item row {0} is invalid.").format(i))
		item_code = _readable_link("Item", row.get("item_code"), _("Item (row {0})").format(i))
		item = frappe.db.get_value("Item", item_code, ["item_name", "disabled", "is_sales_item", "has_variants"],
								   as_dict=True)
		_require(not item.disabled, _("Item {0} is disabled.").format(item_code))
		_require(item.is_sales_item, _("Item {0} is not a sales item.").format(item_code))
		_require(not item.has_variants, _("Item {0} is a template; choose a variant.").format(item_code))
		qty = flt(row.get("qty"))
		_require(qty > 0, _("Quantity must be greater than zero (row {0}).").format(i))
		rate = row.get("rate")
		_require(rate is None or flt(rate) >= 0, _("Rate cannot be negative (row {0}).").format(i))
		warehouse = None
		if row.get("warehouse"):
			warehouse = _company_link("Warehouse", row.get("warehouse"), company, _("Warehouse (row {0})").format(i))
		clean_items.append({"item_code": item_code, "qty": qty,
							"rate": None if rate is None else flt(rate), "warehouse": warehouse})

	normalised = {
		"customer": customer,
		"company": company,
		"transaction_date": str(transaction_date),
		"delivery_date": str(delivery_date),
		"set_warehouse": set_warehouse,
		"taxes_and_charges": taxes_template,
		"po_no": cstr(params.get("po_no") or "")[:140] or None,
		"items": clean_items,
	}

	# Build the order in memory (no database write) with ERPNext's own defaulting, then check every
	# mandatory field of this site's Sales Order (including Amba's property setters, e.g. Set Target
	# Warehouse and Taxes), so the user never confirms a draft that cannot be saved.
	so = _build_sales_order(normalised)
	missing = _missing_mandatory(so)
	_require(not missing, _("Please provide: {0}.").format(", ".join(missing)))

	preview = {
		"doctype": "Sales Order",
		"title": _("Draft Sales Order"),
		"fields": [
			{"label": _("Customer"), "value": customer},
			{"label": _("Company"), "value": company},
			{"label": _("Order Date"), "value": str(transaction_date)},
			{"label": _("Delivery Date"), "value": str(delivery_date)},
			*([{"label": _("Warehouse"), "value": set_warehouse}] if set_warehouse else []),
			*([{"label": _("Tax Template"), "value": taxes_template}] if taxes_template else []),
			*([{"label": _("Customer PO No"), "value": normalised["po_no"]}] if normalised["po_no"] else []),
			{"label": _("Estimated Grand Total"), "value": frappe.utils.fmt_money(so.grand_total, currency=so.currency)},
		],
		"table": {
			"columns": [_("Item"), _("Qty"), _("Rate"), _("Amount")],
			"rows": [[r.item_code, r.qty, r.rate, r.amount] for r in so.items],
		},
		"notes": [_("Prices and taxes are calculated by SaarthiERP and may change if master data changes before you confirm."),
				  _("The order is saved as a Draft. It will not be submitted.")],
	}
	return normalised, preview


def _build_sales_order(params: dict):
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	so = frappe.new_doc("Sales Order")
	so.customer = params["customer"]
	so.company = params["company"]
	so.transaction_date = params["transaction_date"]
	so.delivery_date = params["delivery_date"]
	if params.get("set_warehouse"):
		so.set_warehouse = params["set_warehouse"]
	if params.get("po_no"):
		so.po_no = params["po_no"]
	for row in params["items"]:
		child = {"item_code": row["item_code"], "qty": row["qty"], "delivery_date": params["delivery_date"]}
		if row.get("warehouse") or params.get("set_warehouse"):
			child["warehouse"] = row.get("warehouse") or params["set_warehouse"]
		if row.get("rate") is not None:
			child["rate"] = row["rate"]
		so.append("items", child)
	if params.get("taxes_and_charges"):
		so.taxes_and_charges = params["taxes_and_charges"]
		for tax in get_taxes_and_charges("Sales Taxes and Charges Template", params["taxes_and_charges"]) or []:
			so.append("taxes", tax)
	try:
		so.run_method("set_missing_values")
		so.run_method("calculate_taxes_and_totals")
	except frappe.ValidationError as e:
		frappe.clear_messages()
		raise DraftValidationError(frappe.utils.strip_html(cstr(e)))
	return so


def _missing_mandatory(doc) -> list[str]:
	missing = []
	for df in doc.meta.fields:
		if not df.reqd or df.fieldtype in ("Section Break", "Column Break", "Tab Break"):
			continue
		value = doc.get(df.fieldname)
		if df.fieldtype in ("Table", "Table MultiSelect"):
			if not value:
				missing.append(_(df.label))
		elif value in (None, "") and df.fieldname not in ("naming_series", "status"):
			missing.append(_(df.label))
	return missing


def execute_sales_order(settings, params: dict) -> "frappe.model.document.Document":
	params, _preview = validate_sales_order(settings, params)  # revalidate against current data
	so = _build_sales_order(params)
	so.flags.chikku_ai = True
	so.insert()  # permission-checked insert as the session user; ERPNext validations run here
	so.reload()
	if so.docstatus != 0:  # pragma: no cover - defensive
		raise DraftValidationError(_("Unexpected document state after creation."))
	return so



# ---------------------------------------------------------------- Purchase Invoice (from documents)
PURCHASE_INVOICE_ACTION = "draft.purchase_invoice.create"


def validate_purchase_invoice(settings, params: dict) -> tuple[dict, dict]:
	_require(frappe.has_permission("Purchase Invoice", "create"), _("You are not allowed to create Purchase Invoices."))
	supplier = _readable_link("Supplier", params.get("supplier"), _("Supplier"))
	_require(not frappe.db.get_value("Supplier", supplier, "disabled"), _("Supplier is disabled."))
	company = _readable_link("Company", params.get("company"), _("Company"))
	bill_no = cstr(params.get("bill_no") or "").strip()[:140]
	_require(bill_no, _("Supplier invoice number is required."))
	bill_date = getdate(params.get("bill_date")) if params.get("bill_date") else None
	_require(bill_date, _("Supplier invoice date is required."))
	posting_date = getdate(params.get("posting_date") or today())
	try:
		check_backdate(settings, posting_date, _("Posting date"))
	except BusinessRuleError as e:
		raise DraftValidationError(str(e))
	dup = frappe.get_list("Purchase Invoice", filters={"supplier": supplier, "bill_no": bill_no, "docstatus": ["!=", 2]},
						  pluck="name", limit_page_length=1)
	_require(not dup, _("A Purchase Invoice for this supplier and bill number already exists: {0}.").format(", ".join(dup)))
	taxes_template = None
	if params.get("taxes_and_charges"):
		taxes_template = _company_link("Purchase Taxes and Charges Template", params.get("taxes_and_charges"), company,
									   _("Tax template"))
	purchase_receipt = None
	if params.get("purchase_receipt"):
		purchase_receipt = _readable_link("Purchase Receipt", params.get("purchase_receipt"), _("Purchase Receipt"))
		pr = frappe.db.get_value("Purchase Receipt", purchase_receipt, ["supplier", "company", "docstatus", "per_billed"], as_dict=True)
		_require(pr.docstatus == 1 and pr.supplier == supplier and pr.company == company,
				 _("Purchase Receipt {0} is not a submitted receipt of this supplier and company.").format(purchase_receipt))
		_require(flt(pr.per_billed) < 100, _("Purchase Receipt {0} is already fully billed.").format(purchase_receipt))
	elif frappe.db.get_single_value("Buying Settings", "pr_required") == "Yes":
		raise DraftValidationError(_("On this system a Purchase Invoice must be created against a Purchase Receipt. "
									 "Tell me which Purchase Receipt (goods received) this invoice is for."))
	purchase_order = None
	if purchase_receipt:
		pass
	elif params.get("purchase_order"):
		purchase_order = _readable_link("Purchase Order", params.get("purchase_order"), _("Purchase Order"))
		po = frappe.db.get_value("Purchase Order", purchase_order, ["supplier", "company", "docstatus", "per_billed"], as_dict=True)
		_require(po.docstatus == 1 and po.supplier == supplier and po.company == company,
				 _("Purchase Order {0} is not a submitted order of this supplier and company.").format(purchase_order))
		_require(flt(po.per_billed) < 100, _("Purchase Order {0} is already fully billed.").format(purchase_order))
	elif frappe.db.get_single_value("Buying Settings", "po_required") == "Yes":
		raise DraftValidationError(_("On this system a Purchase Invoice must be created against a Purchase Order. "
									 "Tell me which Purchase Order this invoice belongs to."))
	items = params.get("items") or []
	_require(isinstance(items, list) and 0 < len(items) <= 100, _("Add between 1 and 100 items."))
	clean_items = []
	for i, row in enumerate(items, start=1):
		item_code = _readable_link("Item", row.get("item_code"), _("Item (row {0})").format(i))
		_require(not frappe.db.get_value("Item", item_code, "disabled"), _("Item {0} is disabled.").format(item_code))
		qty = flt(row.get("qty"))
		_require(qty > 0, _("Quantity must be greater than zero (row {0}).").format(i))
		rate = flt(row.get("rate"))
		_require(rate >= 0, _("Rate cannot be negative (row {0}).").format(i))
		clean_items.append({"item_code": item_code, "qty": qty, "rate": rate})
	cost_center = params.get("cost_center") or frappe.get_cached_value("Company", company, "cost_center")
	if cost_center:
		cost_center = _company_link("Cost Center", cost_center, company, _("Cost center"))
		_require(not frappe.db.get_value("Cost Center", cost_center, "is_group"), _("Choose a non-group cost center."))
	normalised = {"supplier": supplier, "company": company, "bill_no": bill_no, "bill_date": str(bill_date),
				  "posting_date": str(posting_date), "taxes_and_charges": taxes_template, "cost_center": cost_center,
				  "purchase_order": purchase_order, "purchase_receipt": purchase_receipt,
				  "source_file": cstr(params.get("source_file") or "")[:140] or None, "items": clean_items}
	pi = _build_purchase_invoice(normalised)
	missing = _missing_mandatory(pi)
	_require(not missing, _("Please provide: {0}.").format(", ".join(missing)))
	expected = flt(params.get("expected_grand_total"))
	notes = [_("The invoice is saved as a Draft and goes through your normal approval workflow. It will not be submitted.")]
	if expected and abs(flt(pi.grand_total) - expected) > max(1, expected * 0.005):
		notes.insert(0, _("SaarthiERP calculates {0} but the document says {1}. Check rates and taxes before confirming.").format(
			frappe.utils.fmt_money(pi.grand_total, currency=pi.currency), frappe.utils.fmt_money(expected, currency=pi.currency)))
	preview = {
		"doctype": "Purchase Invoice", "title": _("Draft Purchase Invoice"),
		"fields": [{"label": _("Supplier"), "value": supplier}, {"label": _("Company"), "value": company},
				   *([{"label": _("Purchase Order"), "value": purchase_order}] if purchase_order else []),
				   *([{"label": _("Purchase Receipt"), "value": purchase_receipt}] if purchase_receipt else []),
				   {"label": _("Supplier Invoice No"), "value": bill_no}, {"label": _("Supplier Invoice Date"), "value": str(bill_date)},
				   {"label": _("Posting Date"), "value": str(posting_date)},
				   *([{"label": _("Cost Center"), "value": cost_center}] if cost_center else []),
				   *([{"label": _("Tax Template"), "value": taxes_template}] if taxes_template else []),
				   {"label": _("Grand Total (SaarthiERP)"), "value": frappe.utils.fmt_money(pi.grand_total, currency=pi.currency)}],
		"table": {"columns": [_("Item"), _("Qty"), _("Rate"), _("Amount")],
				  "rows": [[r.item_code, r.qty, r.rate, r.amount] for r in pi.items]},
		"notes": notes,
	}
	return normalised, preview


def _build_purchase_invoice(params: dict):
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	if params.get("purchase_receipt") or params.get("purchase_order"):
		return _build_purchase_invoice_from_source(params)
	pi = frappe.new_doc("Purchase Invoice")
	pi.supplier, pi.company = params["supplier"], params["company"]
	pi.bill_no, pi.bill_date = params["bill_no"], params["bill_date"]
	pi.posting_date, pi.set_posting_time = params["posting_date"], 1
	pi.update_stock = 0
	if params.get("cost_center"):
		pi.cost_center = params["cost_center"]
	for row in params["items"]:
		pi.append("items", {"item_code": row["item_code"], "qty": row["qty"], "rate": row["rate"],
							**({"cost_center": params["cost_center"]} if params.get("cost_center") else {})})
	if params.get("taxes_and_charges"):
		pi.taxes_and_charges = params["taxes_and_charges"]
		for tax in get_taxes_and_charges("Purchase Taxes and Charges Template", params["taxes_and_charges"]) or []:
			pi.append("taxes", tax)
	try:
		pi.run_method("set_missing_values")
		pi.run_method("calculate_taxes_and_totals")
	except frappe.ValidationError as e:
		frappe.clear_messages()
		raise DraftValidationError(frappe.utils.strip_html(cstr(e)))
	return pi


def _build_purchase_invoice_from_source(params: dict):
	"""ERPNext's own PR/PO -> PI mapping (keeps receipt/order links), quantities and rates from the document."""
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	if params.get("purchase_receipt"):
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

		source = params["purchase_receipt"]
	else:
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice

		source = params["purchase_order"]
	pi = make_purchase_invoice(source)
	wanted = {}
	for row in params["items"]:
		wanted.setdefault(row["item_code"], []).append(row)
	kept = []
	for it in pi.items:
		rows = wanted.get(it.item_code)
		if not rows:
			continue
		row = rows.pop(0)
		it.qty, it.rate = row["qty"], row["rate"]
		if params.get("cost_center"):
			it.cost_center = params["cost_center"]
		kept.append(it)
	missing = [code for code, rows in wanted.items() if rows]
	if missing:
		raise DraftValidationError(_("These items are not open on {0}: {1}").format(source, ", ".join(missing)))
	pi.set("items", kept)
	pi.bill_no, pi.bill_date = params["bill_no"], params["bill_date"]
	pi.posting_date, pi.set_posting_time = params["posting_date"], 1
	pi.update_stock = 0
	# The PO's due date / payment schedule belong to the order; recalculate them for this invoice.
	pi.due_date = None
	pi.set("payment_schedule", [])
	if params.get("cost_center"):
		pi.cost_center = params["cost_center"]
	if params.get("taxes_and_charges"):
		pi.taxes_and_charges = params["taxes_and_charges"]
		pi.set("taxes", [])
		for tax in get_taxes_and_charges("Purchase Taxes and Charges Template", params["taxes_and_charges"]) or []:
			pi.append("taxes", tax)
	try:
		pi.run_method("set_missing_values")
		pi.run_method("calculate_taxes_and_totals")
	except frappe.ValidationError as e:
		frappe.clear_messages()
		raise DraftValidationError(frappe.utils.strip_html(cstr(e)))
	return pi


def execute_purchase_invoice(settings, params: dict):
	params, _preview = validate_purchase_invoice(settings, params)
	pi = _build_purchase_invoice(params)
	pi.flags.chikku_ai = True
	pi.insert()
	pi.reload()
	if params.get("source_file") and frappe.db.exists("File", params["source_file"]):
		src = frappe.get_doc("File", params["source_file"])
		if src.owner == frappe.session.user:
			frappe.get_doc({"doctype": "File", "file_url": src.file_url, "file_name": src.file_name, "is_private": 1,
							"attached_to_doctype": "Purchase Invoice", "attached_to_name": pi.name}).insert()
	if pi.docstatus != 0:  # pragma: no cover
		raise DraftValidationError(_("Unexpected document state after creation."))
	return pi


ACTIONS = {
	SALES_ORDER_ACTION: {
		"doctype": "Sales Order",
		"capability": "draft_actions_enabled",
		"validate": validate_sales_order,
		"execute": execute_sales_order,
	},
	PURCHASE_INVOICE_ACTION: {
		"doctype": "Purchase Invoice",
		"capability": "draft_actions_enabled",
		"validate": validate_purchase_invoice,
		"execute": execute_purchase_invoice,
	},
}


def _register_module_actions():
	from frappe_ai_assistant.frappe_ai_assistant.imports import studio
	from frappe_ai_assistant.frappe_ai_assistant.workflows import proposals

	ACTIONS.setdefault(studio.IMPORT_ACTION, {"doctype": "Chikku AI Import Session", "capability": "import_enabled",
											  "validate": None, "execute": studio.execute_action})
	ACTIONS.setdefault(proposals.APPLY_ACTION, {"doctype": "Chikku AI Workflow Proposal",
												"capability": "workflow_intelligence_enabled", "validate": None,
												"execute": proposals.apply_action})


def get_action(action: str):
	_register_module_actions()
	return ACTIONS.get(action)
