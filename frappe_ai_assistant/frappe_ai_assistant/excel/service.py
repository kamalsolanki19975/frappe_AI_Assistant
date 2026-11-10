"""Excel Intelligence service: generate (from metrics or permitted records), analyse, compare, modify."""

from __future__ import annotations

import json

import frappe
from frappe.utils import cint, now_datetime

from frappe_ai_assistant.frappe_ai_assistant.analytics import metrics
from frappe_ai_assistant.frappe_ai_assistant.services import files
from frappe_ai_assistant.frappe_ai_assistant.services.erp.policy import STANDARD_FIELDS, PolicyError, check_doctype, readable_fields
from frappe_ai_assistant.frappe_ai_assistant.services.erp.query import validate_filters

from . import modify, reader, writer

TYPE_MAP = {"Currency": "currency", "Float": "number", "Int": "integer", "Percent": "number", "Date": "date",
			"Datetime": "date", "Check": "integer"}


# ---------------------------------------------------------------- jobs (activity records)
def start_job(job_type: str, title: str, conversation: str | None, params: dict, input_file: str | None = None):
	job = frappe.get_doc({"doctype": "Chikku AI Job", "job_type": job_type, "title": title[:200], "status": "Running",
						  "user": frappe.session.user, "conversation": conversation, "input_file": input_file,
						  "params_json": json.dumps(params, default=str)[:20000], "started_at": now_datetime()})
	job.insert(ignore_permissions=True)  # code-written activity record owned by the session user
	return job


def finish_job(job, *, status="Completed", output=None, summary=None, result=None, error=None, retryable=0):
	job.update({"status": status, "finished_at": now_datetime(), "progress": 100 if status == "Completed" else job.progress,
				"output_file": (output or {}).get("file"), "output_url": (output or {}).get("url"),
				"summary": (summary or "")[:1000], "result_json": json.dumps(result, default=str)[:60000] if result else None,
				"error": (error or "")[:1000] or None, "retryable": retryable})
	job.save(ignore_permissions=True)


# ---------------------------------------------------------------- generation
def metric_spec(title: str, metric: str, params: dict) -> dict:
	res = metrics.run(metric, params)
	sheet_name = (res["title"] or metric)[:31]
	value_keys = [c["key"] for c in res["columns"] if c["type"] in ("currency", "number", "integer")]
	sheets = [{"name": sheet_name, "columns": res["columns"], "rows": res["rows"],
			   "totals": [k for k in value_keys if not k.endswith("_pct")],
			   "conditional": [{"column": value_keys[-1], "rule": "data_bar"}] if value_keys else [],
			   "chart": {"type": (res.get("chart") or {}).get("type", "column"), "category": res["columns"][0]["key"],
						 "values": value_keys[-1:], "title": res["title"]} if res["rows"] and value_keys else None}]
	for extra_key, extra_title, cols in [
		("consumption", "Material Consumption", [("item_code", "Item", "text"), ("required", "Required", "number"),
												 ("transferred", "Transferred", "number"), ("consumed", "Consumed", "number")]),
		("delayed", "Delayed Work Orders", [("work_order", "Work Order", "text"), ("item", "Item", "text"),
											("planned_end", "Planned End", "text"), ("status", "Status", "text")]),
		("reorder_rows", "Below Reorder Level", [("item_code", "Item", "text"), ("warehouse", "Warehouse", "text"),
												 ("projected", "Projected", "number"), ("level", "Reorder Level", "number"),
												 ("reorder_qty", "Reorder Qty", "number")]),
	]:
		if res.get(extra_key):
			sheets.append({"name": extra_title, "rows": res[extra_key],
						   "columns": [{"key": k, "label": l, "type": t} for k, l, t in cols]})
	source = {**{k: (v if isinstance(v, str) else json.dumps(v, default=str)) for k, v in res["trace"].items()},
			  "metric": metric, "generated_by": frappe.session.user, "generated_on": str(now_datetime())}
	return {"title": title or res["title"], "sheets": sheets,
			"dashboard": {"kpis": res["kpis"], "notes": res.get("notes") or [],
						  "chart": {"sheet": sheet_name, "type": (res.get("chart") or {}).get("type", "column"),
									"category": res["columns"][0]["key"], "values": value_keys[-1:]} if res["rows"] and value_keys else None},
			"source": {**source, "period": res["trace"].get("period") or res["trace"].get("as_of") or ""}}, res


def records_spec(title: str, doctype: str, filters, fields, order_by, limit: int) -> dict:
	meta = check_doctype(doctype)
	readable = readable_fields(meta)
	flt = validate_filters(meta, filters, readable)
	fields = [f for f in (fields or []) if f in readable or f in STANDARD_FIELDS] or \
		["name"] + [f for f in [meta.get_title_field(), "status", "company", "posting_date", "transaction_date",
								"grand_total", "customer", "supplier", "item_name"] if f and f in readable][:10]
	if "name" not in fields:
		fields = ["name", *fields]
	order = "modified desc"
	if order_by:
		parts = str(order_by).split()
		if parts[0] in readable or parts[0] in STANDARD_FIELDS:
			order = f"{parts[0]} {'asc' if len(parts) > 1 and parts[1].lower() == 'asc' else 'desc'}"
	rows = frappe.get_list(doctype, fields=fields, filters=flt, order_by=order, limit_page_length=limit)
	columns = [{"key": f, "label": (readable[f].label if f in readable else f.replace("_", " ").title()),
				"type": TYPE_MAP.get(readable[f].fieldtype, "text") if f in readable else "text"} for f in fields]
	numeric = [c["key"] for c in columns if c["type"] in ("currency", "number")]
	return {"title": title or doctype, "sheets": [{"name": doctype[:31], "columns": columns,
												   "rows": [[r.get(f) for f in fields] for r in rows], "totals": numeric}],
			"source": {"doctype": doctype, "filters": json.dumps([f[1:] for f in flt], default=str),
					   "rows": str(len(rows)), "method": "Permission-checked list of records (only records you can read).",
					   "generated_by": frappe.session.user, "generated_on": str(now_datetime())}}


def generate_report(*, title: str, conversation: str, metric: str | None = None, metric_params: dict | None = None,
					doctype: str | None = None, filters=None, fields=None, order_by=None, limit: int = 1000,
					max_rows: int = 20000) -> dict:
	job = start_job("Analytics Export" if metric else "Excel Report", title or (metric or doctype or "Report"),
					conversation, {"metric": metric, "metric_params": metric_params, "doctype": doctype,
								   "filters": filters, "fields": fields})
	try:
		if metric:
			spec, res = metric_spec(title, metric, metric_params or {})
		elif doctype:
			spec, res = records_spec(title, doctype, filters, fields, order_by, max(1, min(cint(limit) or 1000, max_rows))), None
		else:
			raise metrics.MetricError("Give either a metric or a doctype.")
		content = writer.build(spec)
		out = files.save_output(content, files.safe_filename(spec["title"], "xlsx"), "Chikku AI Conversation", conversation)
		sheets = [s["name"] for s in spec["sheets"]]
		finish_job(job, output=out, summary=f"{len(sheets)} sheet(s): {', '.join(sheets)}",
				   result={"sheets": sheets, "rows": sum(len(s["rows"]) for s in spec["sheets"])})
		return {"job": job.name, "file": out, "sheets": sheets, "rows": sum(len(s["rows"]) for s in spec["sheets"]),
				"has_dashboard": bool(spec.get("dashboard")), "source": spec.get("source"),
				"metric_result": {k: res[k] for k in ("kpis", "trace")} if res else None}
	except (metrics.MetricError, PolicyError, frappe.PermissionError) as e:
		finish_job(job, status="Failed", error=str(e))
		raise


# ---------------------------------------------------------------- analysis
def _load(file_name: str, conversation: str, max_cells: int, keep_formulas: bool = False):
	f = files.owned_attachment(file_name, conversation)
	content = files.read_bytes(f)
	return f, content, reader.load(content, f.file_name, max_cells=max_cells, keep_formulas=keep_formulas)


def analyse(file_name: str, conversation: str, *, focus: str = "summary", sheet: str | None = None,
			columns: list[str] | None = None, max_cells: int = 500000) -> dict:
	f, _content, loaded = _load(file_name, conversation, max_cells)
	job = start_job("Excel Analysis", f"Analysis of {f.file_name}", conversation, {"focus": focus}, input_file=f.name)
	prof = reader.profile(loaded)
	result = {"file": f.file_name, "format": prof["format"], "package": prof["package"],
			  "note": "Values are those last saved in the file; formulas are not recalculated.", "sheets": []}
	for s, raw in zip(prof["sheets"], loaded["sheets"]):
		if sheet and s["name"] != sheet:
			continue
		entry = dict(s)
		if focus == "duplicates":
			headers, data = reader.table(raw)
			cols = [c for c in (columns or []) if c in headers] or headers[:1]
			entry["duplicates_by"] = {"columns": cols, "groups": reader.duplicates_by(headers, data, cols)[:50]}
		result["sheets"].append(entry)
	finish_job(job, summary=f"{len(result['sheets'])} sheet(s) analysed", result={"sheets": [s["name"] for s in result["sheets"]]})
	return result


def compare(file_name: str, conversation: str, *, sheet_a: str, sheet_b: str, key_column: str,
			other_file: str | None = None, max_cells: int = 500000) -> dict:
	_f, _c, la = _load(file_name, conversation, max_cells)
	lb = la if not other_file else _load(other_file, conversation, max_cells)[2]
	sa = next((s for s in la["sheets"] if s["name"] == sheet_a), None)
	sb = next((s for s in lb["sheets"] if s["name"] == sheet_b), None)
	if not sa or not sb:
		raise reader.SpreadsheetError("Sheet not found.")
	ha, da = reader.table(sa)
	hb, db = reader.table(sb)
	if key_column not in ha or key_column not in hb:
		raise reader.SpreadsheetError(f"Key column '{key_column}' must exist in both sheets.")
	ia, ib = ha.index(key_column), hb.index(key_column)
	ma = {str(r[ia]).strip(): dict(zip(ha, r)) for r in da if r[ia] is not None}
	mb = {str(r[ib]).strip(): dict(zip(hb, r)) for r in db if r[ib] is not None}
	common_cols = [c for c in ha if c in hb and c != key_column]
	changed = []
	for k in sorted(set(ma) & set(mb)):
		diffs = {c: [reader._jsonable(ma[k][c]), reader._jsonable(mb[k][c])] for c in common_cols
				 if str(ma[k][c] if ma[k][c] is not None else "") != str(mb[k][c] if mb[k][c] is not None else "")}
		if diffs:
			changed.append({"key": k, "differences": diffs})
	return {"only_in_a": sorted(set(ma) - set(mb))[:200], "only_in_b": sorted(set(mb) - set(ma))[:200],
			"changed": changed[:200], "counts": {"a": len(ma), "b": len(mb), "common": len(set(ma) & set(mb)),
												 "only_a": len(set(ma) - set(mb)), "only_b": len(set(mb) - set(ma)),
												 "changed": len(changed)}}


def modify_workbook(file_name: str, conversation: str, operations: list[dict], *, output_name: str | None = None,
					max_cells: int = 500000) -> dict:
	f, _content, loaded = _load(file_name, conversation, max_cells, keep_formulas=True)
	job = start_job("Excel Modification", f"Revised {f.file_name}", conversation, {"operations": operations}, input_file=f.name)
	try:
		sheets, log = modify.apply(loaded, operations)
		content = modify.write(sheets)
		base = (output_name or f.file_name.rsplit(".", 1)[0] + "_revised")
		out = files.save_output(content, files.safe_filename(base, "xlsx"), "Chikku AI Conversation", conversation)
		warnings = modify.fidelity_warnings(loaded.get("package") or {}, loaded)
		finish_job(job, output=out, summary="; ".join(log)[:1000], result={"changes": log, "warnings": warnings})
		return {"job": job.name, "file": out, "original": f.file_name, "original_unchanged": True,
				"changes": log, "warnings": warnings}
	except (modify.ModifyError, reader.SpreadsheetError) as e:
		finish_job(job, status="Failed", error=str(e))
		raise
