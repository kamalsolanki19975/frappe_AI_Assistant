"""Audit, tool-call and usage records.

These rows are written by code only (their DocTypes grant no create/write permission),
so inserts use ignore_permissions deliberately. They never contain secrets, and
arguments are redacted and size-capped.
"""

import json

import frappe
from frappe.utils import cint, flt

from frappe_ai_assistant.frappe_ai_assistant import AUDIT_LOG, PROVIDER, TOOL_CALL, USAGE_LOG
from frappe_ai_assistant.frappe_ai_assistant.security.redaction import redact

_SEVERITIES = {"Info", "Warning", "High", "Critical"}


def _insert(doc: dict):
	d = frappe.get_doc(doc)
	d.flags.ignore_permissions = True
	d.flags.ignore_links = True
	d.insert(ignore_permissions=True)
	return d


def audit(event_type: str, severity: str = "Info", *, user: str | None = None, conversation: str | None = None,
		  reference_doctype: str | None = None, reference_name: str | None = None, details: dict | None = None,
		  durable: bool | None = None):
	"""Write an audit row.

	durable=True (default for High/Critical) writes through a short background job, so the row
	survives even when the current request is rolled back by the error that follows the event.
	"""
	doc = {
		"doctype": AUDIT_LOG,
		"event_type": event_type[:140],
		"severity": severity if severity in _SEVERITIES else "Info",
		"user": user or frappe.session.user,
		"ip_address": getattr(frappe.local, "request_ip", None),
		"conversation": conversation,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"details_json": json.dumps(redact(details or {}), default=str)[:8000],
	}
	if durable is None:
		durable = severity in ("High", "Critical")
	try:
		if durable and not frappe.flags.chikku_ai_sync_audit and getattr(frappe.local, "request", None):
			frappe.enqueue("frappe_ai_assistant.frappe_ai_assistant.audit.logger.write_audit_job", queue="short", doc=doc)
			return None
		return _insert(doc)
	except Exception:
		# Auditing must never break the user's request, but failures must be visible.
		frappe.log_error(title="Chikku AI: audit write failed")


def write_audit_job(doc: dict):
	if doc.get("conversation") and not frappe.db.exists("Chikku AI Conversation", doc["conversation"]):
		doc["conversation"] = None  # the conversation was created in the rolled-back request
	_insert(doc)


def log_tool_call(*, tool_name, kind, status, args, user, conversation=None, message=None,
				  error_code=None, rows_returned=0, duration_ms=0):
	try:
		return _insert({
			"doctype": TOOL_CALL,
			"tool_name": tool_name[:140],
			"kind": kind,
			"status": status,
			"error_code": error_code,
			"user": user,
			"conversation": conversation,
			"message": message,
			"rows_returned": cint(rows_returned),
			"duration_ms": cint(duration_ms),
			"args_json": json.dumps(redact(args or {}), default=str)[:4000],
		})
	except Exception:
		frappe.log_error(title="Chikku AI: tool call log failed")


def estimate_cost(provider: str | None, model: str | None, input_tokens: int, output_tokens: int):
	"""Cost from admin-configured prices only. Returns (cost, currency) or (0, None)."""
	if not provider or not model:
		return 0.0, None
	row = frappe.db.get_value(
		"Chikku AI Model Price",
		{"parent": provider, "parenttype": PROVIDER, "model": model},
		["input_price_per_million", "output_price_per_million", "currency"],
		as_dict=True,
	)
	if not row:
		return 0.0, None
	cost = (flt(input_tokens) * flt(row.input_price_per_million) + flt(output_tokens) * flt(row.output_price_per_million)) / 1_000_000
	return cost, row.currency


def log_usage(*, user, provider, model, slot, status, input_tokens=0, output_tokens=0, latency_ms=0,
			  error_category=None, conversation=None, message=None):
	cost, currency = estimate_cost(provider, model, input_tokens, output_tokens)
	try:
		_insert({
			"doctype": USAGE_LOG,
			"user": user,
			"provider": provider,
			"model": model,
			"slot": slot,
			"status": status,
			"error_category": error_category,
			"conversation": conversation,
			"message": message,
			"input_tokens": cint(input_tokens),
			"output_tokens": cint(output_tokens),
			"request_count": 1,
			"latency_ms": cint(latency_ms),
			"estimated_cost": cost,
			"currency": currency,
		})
	except Exception:
		frappe.log_error(title="Chikku AI: usage log failed")
	return cost
