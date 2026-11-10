"""Initial tool set: permission-aware ERP reads + one validated draft action."""

from __future__ import annotations

from typing import Any, Literal

import frappe
from pydantic import BaseModel, ConfigDict, Field

from frappe_ai_assistant.frappe_ai_assistant.security.tokens import create_action_request
from frappe_ai_assistant.frappe_ai_assistant.services.erp import query
from frappe_ai_assistant.frappe_ai_assistant.services.erp.drafts import ACTIONS, SALES_ORDER_ACTION, DraftValidationError

from .registry import Tool, ToolContext, register

Filter = list[Any]


class _Strict(BaseModel):
	model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- inputs
class SearchDocumentsIn(_Strict):
	doctype: str = Field(description="SaarthiERP DocType name, e.g. 'Sales Order', 'Purchase Invoice', 'Item'.",
						 max_length=140)
	filters: list[Filter] = Field(default_factory=list, max_length=10,
								  description="List of [fieldname, operator, value]. Operators: =, !=, >, <, >=, <=, "
											  "like, not like, in, not in, between, is. Example: [['status','=','To Deliver and Bill']]")
	search_text: str | None = Field(default=None, max_length=100, description="Free text matched against name/title.")
	fields: list[str] | None = Field(default=None, max_length=15, description="Fieldnames to return (optional).")
	order_by: str | None = Field(default=None, max_length=60, description="'<field> asc' or '<field> desc'.")
	limit: int = Field(default=20, ge=1, le=50)


class GetDocumentIn(_Strict):
	doctype: str = Field(max_length=140)
	name: str = Field(max_length=140, description="Document ID, e.g. 'SAL-ORD-2026-00012'.")
	include_children: bool = True


class EmptyIn(_Strict):
	pass


class PartySearchIn(_Strict):
	search_text: str | None = Field(default=None, max_length=100)
	limit: int = Field(default=20, ge=1, le=50)


class ItemStockIn(_Strict):
	item_code: str = Field(max_length=140)
	warehouse: str | None = Field(default=None, max_length=140)


class SummariseIn(_Strict):
	doctype: str = Field(max_length=140)
	filters: list[Filter] = Field(default_factory=list, max_length=10)
	metric: Literal["count", "sum", "avg", "min", "max"] = "count"
	value_field: str | None = Field(default=None, max_length=140, description="Numeric field for sum/avg/min/max.")
	group_by: str | None = Field(default=None, max_length=140)
	limit: int = Field(default=20, ge=1, le=50)


class SalesOrderItemIn(_Strict):
	item_code: str = Field(max_length=140)
	qty: float = Field(gt=0)
	rate: float | None = Field(default=None, ge=0)
	warehouse: str | None = Field(default=None, max_length=140)


class PrepareSalesOrderIn(_Strict):
	customer: str = Field(max_length=140, description="Customer ID (use search_customers first).")
	company: str = Field(max_length=140)
	delivery_date: str = Field(description="YYYY-MM-DD")
	transaction_date: str | None = Field(default=None, description="YYYY-MM-DD, defaults to today")
	set_warehouse: str | None = Field(default=None, max_length=140,
									  description="Delivery warehouse of the company (required on this site).")
	taxes_and_charges: str | None = Field(default=None, max_length=140,
										  description="Sales Taxes and Charges Template of the company (required on "
													  "this site), e.g. in-state vs out-state GST. Ask the user if unsure.")
	po_no: str | None = Field(default=None, max_length=140)
	items: list[SalesOrderItemIn] = Field(min_length=1, max_length=50)


# ---------------------------------------------------------------- handlers
def _doc_cards(ctx: ToolContext, refs: list, limit: int = 8):
	if refs:
		ctx.blocks.append({"type": "doc_cards", "items": refs[:limit]})


def _table(ctx: ToolContext, result: dict, max_rows: int = 15):
	rows = result.get("rows") or []
	if not rows:
		return
	cols = list(rows[0].keys())[:8]
	ctx.blocks.append({
		"type": "table",
		"title": result.get("doctype"),
		"doctype": result.get("doctype"),
		"columns": cols,
		"rows": [[r.get(c) for c in cols] for r in rows[:max_rows]],
		"truncated": bool(result.get("truncated")) or len(rows) > max_rows,
	})


def search_documents(a: SearchDocumentsIn, ctx: ToolContext):
	res = query.search(a.doctype, filters=a.filters, search_text=a.search_text, fields=a.fields,
					   order_by=a.order_by, limit=a.limit)
	_table(ctx, res)
	return res


def get_document(a: GetDocumentIn, ctx: ToolContext):
	res = query.get_document(a.doctype, a.name, include_children=a.include_children)
	_doc_cards(ctx, [{"doctype": a.doctype, "name": res["name"],
					  "title": res.get(frappe.get_meta(a.doctype).get_title_field()) or res["name"],
					  "status": res.get("status")}])
	return res


def get_current_context(_a: EmptyIn, ctx: ToolContext):
	if not ctx.context:
		return {"context": None, "message": "The user is not on a specific document or list."}
	return {"context": ctx.context}


def _party_search(doctype, fields):
	def handler(a: PartySearchIn, ctx: ToolContext):
		res = query.search(doctype, search_text=a.search_text, fields=fields, limit=a.limit,
						   order_by="modified desc")
		_table(ctx, res)
		return res

	return handler


def item_stock(a: ItemStockIn, ctx: ToolContext):
	res = query.item_stock(a.item_code, a.warehouse)
	if res["warehouses"]:
		ctx.blocks.append({
			"type": "table", "title": f"Stock — {a.item_code}",
			"columns": ["warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"],
			"rows": [[w.get(c) for c in ("warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty")]
					 for w in res["warehouses"][:15]],
			"truncated": len(res["warehouses"]) > 15,
		})
	return res


def summarise(a: SummariseIn, ctx: ToolContext):
	res = query.aggregate(a.doctype, filters=a.filters, group_by=a.group_by, metric=a.metric,
						  value_field=a.value_field, limit=a.limit)
	if a.group_by and res["rows"]:
		labels = [str(r.get(a.group_by) or "—")[:40] for r in res["rows"][:12]]
		values = [float(r.get("value") or 0) for r in res["rows"][:12]]
		ctx.blocks.append({"type": "chart", "title": f"{a.metric} of {a.value_field or a.doctype} by {a.group_by}",
						   "chart_type": "bar", "labels": labels, "datasets": [{"name": a.metric, "values": values}]})
	return res


def prepare_sales_order(a: PrepareSalesOrderIn, ctx: ToolContext):
	spec = ACTIONS[SALES_ORDER_ACTION]
	try:
		params, preview = spec["validate"](ctx.settings, a.model_dump())
	except DraftValidationError as e:
		return {"error": "VALIDATION", "message": str(e)}
	summary = f"Draft Sales Order for {params['customer']} ({len(params['items'])} item(s))"
	name, token = create_action_request(
		action=SALES_ORDER_ACTION, params=params, preview=preview, summary=summary, target_doctype=spec["doctype"],
		conversation=ctx.conversation, message=ctx.message,
		expiry_minutes=ctx.settings.action_expiry_minutes or 10,
	)
	ctx.blocks.append({"type": "action_preview", "action_request": name, "summary": summary, "preview": preview,
					   "expiry_minutes": ctx.settings.action_expiry_minutes or 10})
	ctx.action_tokens.append({"action_request": name, "token": token})  # browser only
	return {
		"status": "PREVIEW_SHOWN",
		"action_request": name,
		"message": "A preview is now shown to the user with a Confirm button. Nothing has been created yet. "
		"Tell the user to review and confirm. Do not claim the order exists.",
	}


# ---------------------------------------------------------------- registration
register(Tool("search_documents",
			  "Search SaarthiERP records the user may read. Returns matching rows with key fields. Use filters for "
			  "exact conditions and search_text for names. Results are capped.",
			  SearchDocumentsIn, search_documents))
register(Tool("get_document",
			  "Get one SaarthiERP document (with child table rows) that the user may read.",
			  GetDocumentIn, get_document))
register(Tool("get_current_context",
			  "Get the validated details of the document or list the user currently has open in SaarthiERP.",
			  EmptyIn, get_current_context, capability="erp_context_enabled"))
register(Tool("search_customers", "Search customers by name or ID.", PartySearchIn,
			  _party_search("Customer", ["customer_name", "customer_group", "territory", "disabled"])))
register(Tool("search_suppliers", "Search suppliers by name or ID.", PartySearchIn,
			  _party_search("Supplier", ["supplier_name", "supplier_group", "country", "disabled"])))
register(Tool("search_items", "Search items by item code or name.", PartySearchIn,
			  _party_search("Item", ["item_name", "item_group", "stock_uom", "is_stock_item", "disabled"])))
register(Tool("get_item_stock",
			  "Get current stock (actual, reserved, ordered, projected qty) of one item per warehouse.",
			  ItemStockIn, item_stock))
register(Tool("summarise_records",
			  "Count or total records the user may read, optionally grouped by a field. For DocTypes with a "
			  "company field a company filter is required. Use docstatus=1 for submitted documents only.",
			  SummariseIn, summarise))
register(Tool("prepare_sales_order_draft",
			  "Prepare a DRAFT Sales Order and show the user a preview with a Confirm button. This does not create "
			  "anything by itself; only the user's click can. Resolve customer, items, warehouse and tax template "
			  "with the search tools first. If the tool says fields are missing, ask the user for them.",
			  PrepareSalesOrderIn, prepare_sales_order, kind="draft", capability="draft_actions_enabled",
			  audit="write"))
