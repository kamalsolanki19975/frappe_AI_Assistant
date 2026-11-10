"""Workbook modification. The original upload is never changed: a new workbook is written with
XlsxWriter from the source values (and source formulas, kept as formulas with their cached values).

Supported operations (validated, deterministic):
	remove_duplicates {sheet, columns?}
	filter_rows       {sheet, column, operator, value}
	add_formula_column{sheet, header, expression}    e.g. "{Qty} * {Rate}"  (column refs only, + - * / ( ) numbers)
	add_summary_sheet {sheet, group_by, sum_columns[]}
	combine_sheets    {sheets[], name}
	add_chart         {sheet, category, values[], type}
	sort_rows         {sheet, column, descending}
	format            {}   (header styling, widths, freeze panes, filters on every sheet)
"""

from __future__ import annotations

import re

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from .reader import table

ALLOWED_EXPR = re.compile(r"^[\s\d.+\-*/()]*$")
OPS = {"=", "!=", ">", "<", ">=", "<=", "contains", "is empty", "is not empty"}


class ModifyError(Exception):
	"""Message is safe to show to the user."""


def fidelity_warnings(package: dict, loaded: dict) -> list[str]:
	w = []
	if package.get("charts"):
		w.append(f"The original has {package['charts']} chart(s); they are not copied. Ask me to add new charts if needed.")
	if package.get("pivot_tables"):
		w.append(f"The original has {package['pivot_tables']} pivot table(s); they become plain values in the new file.")
	if package.get("images") or package.get("embedded_objects"):
		w.append("Images and embedded objects in the original are not copied.")
	if package.get("external_links") or package.get("data_connections"):
		w.append("External links / data connections are not copied.")
	n_formulas = sum(len(s.get("formulas") or {}) for s in loaded["sheets"])
	if n_formulas:
		w.append(f"{n_formulas} formula cell(s) are kept as formulas. Their values shown are the ones last saved by "
				 "Excel; Excel recalculates when the file is opened (Chikku AI does not recalculate formulas).")
	if any(s["truncated"] for s in loaded["sheets"]):
		w.append("The workbook exceeded the processing limit, so only the first part was used.")
	w.append("Cell styles (fonts, colours, merged cells) from the original are not preserved; a clean format is applied.")
	return w


def _sheets(loaded: dict) -> dict:
	out = {}
	for s in loaded["sheets"]:
		headers, data = table(s, header_row=0) if s["rows"] else ([], [])
		# keep formula strings aligned with data rows (header row 0 -> data row i is source row i+1)
		formulas = {(r - 1, c): f for (r, c), f in (s.get("formulas") or {}).items() if r >= 1}
		out[s["name"]] = {"headers": headers, "rows": [list(r) for r in data], "formulas": formulas, "charts": []}
	return out


def _get(sheets, name):
	if name not in sheets:
		raise ModifyError(f"Sheet '{name}' not found. Sheets: {', '.join(sheets)}")
	return sheets[name]


def _col(sheet, name):
	if name not in sheet["headers"]:
		raise ModifyError(f"Column '{name}' not found. Columns: {', '.join(sheet['headers'][:30])}")
	return sheet["headers"].index(name)


def apply(loaded: dict, operations: list[dict]) -> tuple[dict, list[str]]:
	sheets = _sheets(loaded)
	log = []
	for op in operations[:20]:
		kind = op.get("op")
		if kind == "remove_duplicates":
			s = _get(sheets, op["sheet"])
			idx = [_col(s, c) for c in op.get("columns") or s["headers"]]
			seen, keep, removed = set(), [], 0
			for r in s["rows"]:
				key = tuple(str(r[i]).strip().lower() if r[i] is not None else "" for i in idx)
				if key in seen:
					removed += 1
					continue
				seen.add(key)
				keep.append(r)
			s["rows"], s["formulas"] = keep, {}
			log.append(f"{op['sheet']}: removed {removed} duplicate row(s)"
					   + (f" based on {', '.join(op['columns'])}" if op.get("columns") else ""))
		elif kind == "filter_rows":
			s = _get(sheets, op["sheet"])
			ci, oper, val = _col(s, op["column"]), str(op.get("operator", "=")), op.get("value")
			if oper not in OPS:
				raise ModifyError(f"Unsupported operator {oper}")
			before = len(s["rows"])
			s["rows"] = [r for r in s["rows"] if _match(r[ci], oper, val)]
			s["formulas"] = {}
			log.append(f"{op['sheet']}: kept {len(s['rows'])} of {before} row(s) where {op['column']} {oper} {val}")
		elif kind == "sort_rows":
			s = _get(sheets, op["sheet"])
			ci = _col(s, op["column"])
			s["rows"].sort(key=lambda r: (r[ci] is None, _sortable(r[ci])), reverse=bool(op.get("descending")))
			s["formulas"] = {}
			log.append(f"{op['sheet']}: sorted by {op['column']}{' (descending)' if op.get('descending') else ''}")
		elif kind == "add_formula_column":
			s = _get(sheets, op["sheet"])
			expr = str(op["expression"])
			refs = re.findall(r"\{([^}]+)\}", expr)
			if not refs or not ALLOWED_EXPR.match(re.sub(r"\{[^}]+\}", "1", expr)):
				raise ModifyError("Formulas may only combine columns like {Qty} * {Rate} with + - * / and numbers.")
			cols = {ref: _col(s, ref) for ref in refs}
			new_ci = len(s["headers"])
			s["headers"].append(str(op["header"])[:80])
			for r_i, row in enumerate(s["rows"]):
				excel_row = r_i + 2
				formula = "=" + re.sub(r"\{([^}]+)\}", lambda m: f"{xl_col_to_name(cols[m.group(1)])}{excel_row}", expr)
				row.append(None)
				s["formulas"][(r_i, new_ci)] = formula
			log.append(f"{op['sheet']}: added formula column '{op['header']}' = {expr}")
		elif kind == "add_summary_sheet":
			s = _get(sheets, op["sheet"])
			gi = _col(s, op["group_by"])
			sums = [(c, _col(s, c)) for c in op.get("sum_columns") or []]
			groups = {}
			for r in s["rows"]:
				key = r[gi] if r[gi] is not None else "(blank)"
				g = groups.setdefault(key, [0, *[0.0 for _ in sums]])
				g[0] += 1
				for k, (_c, ci) in enumerate(sums):
					if isinstance(r[ci], (int, float)) and not isinstance(r[ci], bool):
						g[k + 1] += r[ci]
			name = (op.get("name") or f"Summary by {op['group_by']}")[:31]
			sheets[name] = {"headers": [op["group_by"], "Rows", *[f"Total {c}" for c, _ in sums]],
							"rows": [[k, *v] for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))],
							"formulas": {}, "charts": [], "totals": True}
			log.append(f"Added sheet '{name}' grouping {op['sheet']} by {op['group_by']}")
		elif kind == "combine_sheets":
			names = op.get("sheets") or []
			src = [_get(sheets, n) for n in names]
			headers = []
			for s in src:
				for h in s["headers"]:
					if h not in headers:
						headers.append(h)
			rows = []
			for n, s in zip(names, src):
				for r in s["rows"]:
					d = dict(zip(s["headers"], r))
					rows.append([n, *[d.get(h) for h in headers]])
			name = (op.get("name") or "Combined")[:31]
			sheets[name] = {"headers": ["Source Sheet", *headers], "rows": rows, "formulas": {}, "charts": []}
			log.append(f"Added sheet '{name}' combining {len(rows)} row(s) from {', '.join(names)}")
		elif kind == "add_chart":
			s = _get(sheets, op["sheet"])
			_col(s, op["category"])
			for v in op.get("values") or []:
				_col(s, v)
			s["charts"].append({"type": op.get("type", "column"), "category": op["category"], "values": op.get("values") or []})
			log.append(f"{op['sheet']}: added {op.get('type', 'column')} chart of {', '.join(op.get('values') or [])}")
		elif kind == "format":
			log.append("Applied header styling, column widths, filters and frozen header row")
		else:
			raise ModifyError(f"Unsupported operation '{kind}'")
	return sheets, log


def _sortable(v):
	return (0, v) if isinstance(v, (int, float)) else (1, str(v))


def _match(cell, oper, val):
	if oper == "is empty":
		return cell in (None, "")
	if oper == "is not empty":
		return cell not in (None, "")
	if oper == "contains":
		return val is not None and str(val).lower() in str(cell or "").lower()
	a, b = cell, val
	try:
		if isinstance(a, (int, float)) or isinstance(b, (int, float)):
			a, b = float(a), float(b)
	except (TypeError, ValueError):
		a, b = str(a), str(b)
	if oper in ("=", "!="):
		eq = str(a).strip().lower() == str(b).strip().lower() if isinstance(a, str) else a == b
		return eq if oper == "=" else not eq
	try:
		return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[oper]
	except TypeError:
		return False


def write(sheets: dict) -> bytes:
	import io

	buf = io.BytesIO()
	wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
	header = wb.add_format({"bold": True, "font_color": "#ffffff", "bg_color": "#4f46e5", "border": 1})
	date_fmt = wb.add_format({"num_format": "dd-mm-yyyy"})
	import datetime as dt

	for name, s in sheets.items():
		ws = wb.add_worksheet(name[:31])
		for c, h in enumerate(s["headers"]):
			ws.write_string(0, c, str(h), header)
			ws.set_column(c, c, min(max(len(str(h)) + 4, 10), 45))
		for r_i, row in enumerate(s["rows"]):
			for c_i, v in enumerate(row):
				f = s["formulas"].get((r_i, c_i))
				if f:
					ws.write_formula(r_i + 1, c_i, f, None, v if v is not None else 0)
				elif v is None:
					continue
				elif isinstance(v, bool):
					ws.write_boolean(r_i + 1, c_i, v)
				elif isinstance(v, (int, float)):
					ws.write_number(r_i + 1, c_i, v)
				elif isinstance(v, (dt.date, dt.datetime)):
					ws.write_datetime(r_i + 1, c_i, v if isinstance(v, dt.datetime) else dt.datetime.combine(v, dt.time()), date_fmt)
				else:
					ws.write_string(r_i + 1, c_i, str(v))
		if s["headers"]:
			ws.autofilter(0, 0, max(len(s["rows"]), 1), len(s["headers"]) - 1)
			ws.freeze_panes(1, 0)
		for chart_spec in s.get("charts") or []:
			chart = wb.add_chart({"type": chart_spec["type"] if chart_spec["type"] in ("column", "bar", "line", "pie") else "column"})
			cat = s["headers"].index(chart_spec["category"])
			last = min(len(s["rows"]), 50)
			for v in chart_spec["values"][:4]:
				vi = s["headers"].index(v)
				chart.add_series({"name": v, "categories": [name[:31], 1, cat, last, cat],
								  "values": [name[:31], 1, vi, last, vi]})
			ws.insert_chart(1, len(s["headers"]) + 1, chart)
	wb.close()
	return buf.getvalue()
