"""Safe spreadsheet reading (XLSX via openpyxl read-only, CSV via csv) and profiling.

Formulas are never evaluated: openpyxl returns the value cached by Excel at last save
(data_only=True). Reports say so explicitly.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import statistics
import zipfile
from collections import Counter

from frappe import _

MAX_UNCOMPRESSED = 150 * 1024 * 1024
MAX_RATIO = 120
MAX_MEMBERS = 2000
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


class SpreadsheetError(Exception):
	"""Message is safe to show to the user."""


def inspect_package(content: bytes) -> dict:
	"""Zip-bomb guard + fidelity features of an XLSX package (without parsing XML)."""
	try:
		zf = zipfile.ZipFile(io.BytesIO(content))
	except zipfile.BadZipFile:
		raise SpreadsheetError(_("This is not a valid .xlsx file."))
	infos = zf.infolist()
	if len(infos) > MAX_MEMBERS:
		raise SpreadsheetError(_("The workbook has too many internal parts to be processed safely."))
	total = 0
	for info in infos:
		total += info.file_size
		if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO and info.file_size > 5_000_000:
			raise SpreadsheetError(_("The workbook looks malformed (suspicious compression)."))
		if info.filename.lower().endswith((".zip", ".xlsx", ".xlsm")):
			raise SpreadsheetError(_("Nested archives are not allowed."))
	if total > MAX_UNCOMPRESSED:
		raise SpreadsheetError(_("The workbook is too large to process safely."))
	names = [i.filename for i in infos]
	return {
		"macros": any(n.endswith("vbaProject.bin") for n in names),
		"charts": sum(1 for n in names if n.startswith("xl/charts/chart")),
		"pivot_tables": sum(1 for n in names if n.startswith("xl/pivotTables/")),
		"images": sum(1 for n in names if n.startswith("xl/media/")),
		"external_links": sum(1 for n in names if n.startswith("xl/externalLinks/")),
		"embedded_objects": sum(1 for n in names if n.startswith("xl/embeddings/")),
		"data_connections": any(n == "xl/connections.xml" for n in names),
	}


def _clean(v):
	if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
		return int(v)
	if isinstance(v, str):
		v = v.strip()
		return v if v != "" else None
	if isinstance(v, dt.datetime) and v.time() == dt.time(0, 0):
		return v.date()
	return v


def load(content: bytes, file_name: str, *, max_cells: int = 500_000, keep_formulas: bool = False) -> dict:
	"""Returns {"sheets": [{"name", "rows": [[...]], "truncated", "formulas": {(r,c): "=..."}}], "package": {...}}."""
	ext = file_name.rsplit(".", 1)[-1].lower()
	if ext == "csv":
		return {"sheets": [_load_csv(content, max_cells)], "package": {}, "format": "csv"}
	if ext not in ("xlsx",):
		raise SpreadsheetError(_("Only .xlsx and .csv files are supported for spreadsheet work."))
	package = inspect_package(content)
	if package["macros"]:
		raise SpreadsheetError(_("Workbooks containing macros are not processed."))
	import openpyxl

	wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
	formula_wb = None
	if keep_formulas:
		formula_wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
	sheets, cells = [], 0
	try:
		for ws in wb.worksheets:
			rows, truncated, formulas = [], False, {}
			fws = formula_wb[ws.title] if formula_wb else None
			frows = fws.iter_rows(values_only=True) if fws else None
			for r_idx, row in enumerate(ws.iter_rows(values_only=True)):
				frow = next(frows, None) if frows else None
				cells += len(row)
				if cells > max_cells:
					truncated = True
					break
				rows.append([_clean(v) for v in row])
				if frow:
					for c_idx, fv in enumerate(frow):
						if isinstance(fv, str) and fv.startswith("="):
							formulas[(r_idx, c_idx)] = fv
			while rows and all(v is None for v in rows[-1]):
				rows.pop()
			width = max((len(r) for r in rows), default=0)
			# trim empty trailing columns
			while width and all((len(r) < width or r[width - 1] is None) for r in rows):
				width -= 1
			rows = [(r + [None] * width)[:width] for r in rows]
			sheets.append({"name": ws.title, "rows": rows, "truncated": truncated, "formulas": formulas})
			if truncated:
				break
	finally:
		wb.close()
		if formula_wb:
			formula_wb.close()
	return {"sheets": sheets, "package": package, "format": "xlsx"}


def _load_csv(content: bytes, max_cells: int) -> dict:
	for enc in ("utf-8-sig", "cp1252"):
		try:
			text = content.decode(enc)
			break
		except UnicodeDecodeError:
			continue
	else:
		raise SpreadsheetError(_("Could not read the CSV text encoding."))
	try:
		dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
	except csv.Error:
		dialect = csv.excel
	rows, cells, truncated = [], 0, False
	for row in csv.reader(io.StringIO(text), dialect):
		cells += len(row)
		if cells > max_cells:
			truncated = True
			break
		rows.append([_clean(_coerce(v)) for v in row])
	width = max((len(r) for r in rows), default=0)
	rows = [(r + [None] * width)[:width] for r in rows]
	return {"name": "CSV", "rows": rows, "truncated": truncated, "formulas": {}}


def _coerce(v: str):
	v = v.strip()
	if re.fullmatch(r"-?\d+", v) and len(v) < 16 and not (len(v) > 1 and v.startswith("0")):
		return int(v)
	if re.fullmatch(r"-?\d+\.\d+", v):
		return float(v)
	return v


def detect_header(rows: list[list]) -> int:
	"""Index of the header row: first of the first 10 rows that is mostly non-empty text."""
	best, best_score = 0, -1.0
	for i, row in enumerate(rows[:10]):
		filled = [v for v in row if v is not None]
		if not filled:
			continue
		text_ratio = sum(isinstance(v, str) for v in filled) / len(filled)
		fill_ratio = len(filled) / max(len(row), 1)
		score = text_ratio * 0.6 + fill_ratio * 0.4
		if score > best_score + 0.05:
			best, best_score = i, score
		if score >= 0.95:
			break
	return best


def table(sheet: dict, header_row: int | None = None) -> tuple[list[str], list[list]]:
	rows = sheet["rows"]
	if not rows:
		return [], []
	h = detect_header(rows) if header_row is None else header_row
	headers, seen = [], Counter()
	for i, v in enumerate(rows[h]):
		name = str(v).strip() if v is not None else f"Column {i + 1}"
		seen[name] += 1
		headers.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
	data = [r for r in rows[h + 1:] if any(v is not None for v in r)]
	return headers, data


def column_type(values: list) -> str:
	vals = [v for v in values if v is not None]
	if not vals:
		return "empty"
	kinds = Counter(
		"number" if isinstance(v, (int, float)) and not isinstance(v, bool) else
		"date" if isinstance(v, (dt.date, dt.datetime)) else
		"bool" if isinstance(v, bool) else "text"
		for v in vals
	)
	kind, n = kinds.most_common(1)[0]
	return kind if n / len(vals) >= 0.9 else "mixed"


def profile(loaded: dict, max_sample: int = 5) -> dict:
	out = {"sheets": [], "package": loaded.get("package") or {}, "format": loaded.get("format")}
	for sheet in loaded["sheets"]:
		headers, data = table(sheet)
		cols = []
		for i, hname in enumerate(headers):
			values = [r[i] for r in data]
			ctype = column_type(values)
			filled = [v for v in values if v is not None]
			col = {"name": hname, "type": ctype, "empty": len(values) - len(filled),
				   "distinct": len({str(v) for v in filled})}
			nums = [float(v) for v in filled if isinstance(v, (int, float)) and not isinstance(v, bool)]
			if ctype == "number" and nums:
				col.update({"sum": round(sum(nums), 4), "min": min(nums), "max": max(nums),
							"mean": round(statistics.fmean(nums), 4)})
				outliers = find_outliers(nums)
				if outliers:
					col["outliers"] = outliers[:5]
			if "gst" in hname.lower():
				bad = [str(v) for v in filled if not GSTIN_RE.match(str(v).strip().upper())]
				col["invalid_gstin"] = len(bad)
				col["invalid_gstin_examples"] = bad[:5]
			cols.append(col)
		dup_rows = _duplicate_rows(data)
		out["sheets"].append({
			"name": sheet["name"], "rows": len(data), "columns": cols, "truncated": sheet["truncated"],
			"duplicate_rows": len(dup_rows), "duplicate_examples": dup_rows[:5],
			"formula_cells": len(sheet.get("formulas") or {}),
			"sample": [dict(zip(headers, [_jsonable(v) for v in r])) for r in data[:max_sample]],
		})
	return out


def find_outliers(nums: list[float]) -> list[float]:
	if len(nums) < 8:
		return []
	q = statistics.quantiles(nums, n=4)
	iqr = q[2] - q[0]
	if iqr <= 0:
		return []
	lo, hi = q[0] - 3 * iqr, q[2] + 3 * iqr
	return [v for v in nums if v < lo or v > hi]


def _duplicate_rows(data: list[list]) -> list[int]:
	seen, dups = {}, []
	for i, r in enumerate(data):
		key = tuple(str(v).strip().lower() if v is not None else "" for v in r)
		if key in seen:
			dups.append(i + 2)  # approx. sheet row number (header = row 1)
		else:
			seen[key] = i
	return dups


def duplicates_by(headers: list[str], data: list[list], columns: list[str]) -> list[dict]:
	idx = [headers.index(c) for c in columns]
	groups: dict[tuple, list[int]] = {}
	for i, r in enumerate(data):
		key = tuple(str(r[j]).strip().lower() if r[j] is not None else "" for j in idx)
		if all(k == "" for k in key):
			continue
		groups.setdefault(key, []).append(i)
	return [{"value": {c: _jsonable(data[rows[0]][j]) for c, j in zip(columns, idx)}, "rows": [r + 2 for r in rows]}
			for rows in groups.values() if len(rows) > 1]


def _jsonable(v):
	if isinstance(v, (dt.date, dt.datetime)):
		return v.isoformat()
	return v
