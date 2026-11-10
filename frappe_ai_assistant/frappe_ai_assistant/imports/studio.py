"""Conversational Data Import Studio.

Flow: start (parse + eligibility + proposed mapping) -> user edits mapping/defaults in the panel ->
validate (background: types, links, mandatory, duplicates, existing records, company, dry-run of ERPNext
validations in a rolled-back savepoint) -> preview -> confirmation token (single use, bound to user, DocType,
file hash, mapping hash, selected rows) -> native Frappe Data Import executes as the user
(submit_after_import forced off; per-record commits) -> results, error report, safe retry.

Permission model (verified on this site): the user needs native Data Import access (System Manager or the
site's "Data Import and Export" role) AND native Import permission on the target DocType. Chikku adds an
administrator allow-list of DocTypes on top. Nothing uses ignore_permissions to force records in.
"""

from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, now_datetime

from frappe_ai_assistant.frappe_ai_assistantimport REALTIME_EVENT
from frappe_ai_assistant.frappe_ai_assistant.excel import reader
from frappe_ai_assistant.frappe_ai_assistant.services import files
from frappe_ai_assistant.frappe_ai_assistant.services.erp.policy import DENY_DOCTYPES, DENY_PREFIXES

SESSION = "Chikku AI Import Session"
SKIP_TYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Image", "Fold", "Heading", "Table",
			  "Table MultiSelect", "Attach", "Attach Image", "Signature", "Geolocation", "Password", "Code",
			  "HTML Editor", "Text Editor", "Markdown Editor", "JSON", "Barcode", "Icon"}
AUTO_FIELDS = {"naming_series", "company", "currency", "conversion_rate", "price_list_currency", "plc_conversion_rate",
			   "selling_price_list", "buying_price_list", "status", "posting_time", "posting_date", "transaction_date",
			   "order_type", "uom", "conversion_factor", "stock_uom", "item_name", "description", "cost_center",
			   "credit_to", "debit_to", "language", "is_stock_item"}
SYNONYMS = {
	"gst": "gstin", "gstin": "gstin", "gst no": "gstin", "gst number": "gstin", "pan": "pan",
	"name": "__title__", "customer": "customer_name", "supplier": "supplier_name", "party": "__title__",
	"mobile": "mobile_no", "phone": "mobile_no", "email": "email_id", "e-mail": "email_id",
	"qty": "qty", "quantity": "qty", "pcs": "qty", "nos": "qty", "rate": "rate", "price": "rate",
	"item": "item_code", "item code": "item_code", "sku": "item_code", "style no": "item_code", "hsn": "gst_hsn_code",
	"hsn code": "gst_hsn_code", "uom": "uom", "unit": "uom", "group": "__group__", "category": "__group__",
	"state": "state", "city": "city", "pincode": "pincode", "pin": "pincode", "address": "address_line1",
}


class ImportError_(frappe.ValidationError):
	pass


# ---------------------------------------------------------------- eligibility registry
def eligibility(doctype: str, settings=None) -> dict:
	"""Why a DocType can or cannot be imported by the session user. Dynamic + admin allow-list."""
	settings = settings or frappe.get_cached_doc("Chikku AI Settings")
	from frappe.core.doctype.data_import.data_import import BLOCKED_DOCTYPES

	approved = {r.reference_doctype: r for r in settings.import_doctypes or []}
	reasons = []
	if not doctype or not frappe.db.exists("DocType", doctype):
		return {"doctype": doctype, "eligible": False, "reasons": ["DocType not found."]}
	meta = frappe.get_meta(doctype)
	if doctype not in approved:
		reasons.append("Not approved for Chikku AI import by the administrator (Chikku AI Settings > Advanced Modules).")
	if meta.istable or meta.issingle or getattr(meta, "is_virtual", 0):
		reasons.append("Child, single and virtual DocTypes cannot be imported on their own.")
	if not meta.allow_import:
		reasons.append("Import is disabled for this DocType.")
	if doctype in BLOCKED_DOCTYPES or doctype in DENY_DOCTYPES or doctype.startswith(DENY_PREFIXES):
		reasons.append("System or security DocType.")
	if not frappe.has_permission("Data Import", "create"):
		reasons.append("You need access to Data Import (role 'Data Import and Export' or System Manager).")
	if not frappe.has_permission(doctype, "import"):
		reasons.append(f"You don't have Import permission on {doctype}.")
	row = approved.get(doctype)
	modes = []
	if row and row.allow_insert and frappe.has_permission(doctype, "create"):
		modes.append("Insert New Records")
	if row and row.allow_update and frappe.has_permission(doctype, "write"):
		modes.append("Update Existing Records")
	if row and not modes:
		reasons.append("You don't have create/write permission for the allowed import modes.")
	return {"doctype": doctype, "eligible": not reasons, "reasons": reasons, "modes": modes,
			"is_submittable": bool(meta.is_submittable)}


def eligible_doctypes(settings=None) -> list[dict]:
	settings = settings or frappe.get_cached_doc("Chikku AI Settings")
	return [e for e in (eligibility(r.reference_doctype, settings) for r in settings.import_doctypes or []) if e["eligible"]]


# ---------------------------------------------------------------- fields
def importable_fields(doctype: str) -> list[dict]:
	meta = frappe.get_meta(doctype)
	writable = set(meta.get_permitted_fieldnames(user=frappe.session.user, permission_type="write") or []) or None
	out = [{"value": "name", "label": "ID (name)", "fieldtype": "Data", "reqd": 0, "options": None, "table": None}]
	for df in meta.fields:
		if df.fieldtype in SKIP_TYPES or df.read_only or df.hidden and not df.reqd:
			continue
		if writable is not None and df.fieldname not in writable:
			continue
		out.append({"value": df.fieldname, "label": _(df.label or df.fieldname), "fieldtype": df.fieldtype,
					"reqd": cint(df.reqd), "options": df.options if df.fieldtype in ("Link", "Select") else None, "table": None})
	for tdf in meta.get_table_fields():
		if tdf.fieldtype != "Table":
			continue
		cmeta = frappe.get_meta(tdf.options)
		for df in cmeta.fields:
			if df.fieldtype in SKIP_TYPES or df.read_only or df.hidden:
				continue
			out.append({"value": f"{tdf.fieldname}.{df.fieldname}", "label": f"{_(df.label or df.fieldname)} ({_(tdf.label)})",
						"fieldtype": df.fieldtype, "reqd": cint(df.reqd), "table": tdf.fieldname,
						"options": df.options if df.fieldtype in ("Link", "Select") else None})
	return out


def _norm(s: str) -> str:
	return re.sub(r"[^a-z0-9]+", " ", cstr(s).lower()).strip()


def propose_mapping(doctype: str, headers: list[str], data: list[list]) -> list[dict]:
	fields = importable_fields(doctype)
	meta = frappe.get_meta(doctype)
	by_key = {}
	for f in fields:
		for key in (f["value"], f["label"], f["value"].split(".")[-1].replace("_", " ")):
			by_key.setdefault(_norm(key), f["value"])
	title = meta.get_title_field() if meta.get_title_field() != "name" else None
	group_field = next((f["value"] for f in fields if f["value"].endswith("_group") and not f["table"]), None)
	used, out = set(), []
	for i, h in enumerate(headers):
		n = _norm(h)
		field, conf, reason = None, 0.0, None
		if n in by_key:
			field, conf, reason = by_key[n], 1.0, "exact name match"
		elif n in SYNONYMS:
			target = SYNONYMS[n]
			target = title if target == "__title__" else group_field if target == "__group__" else target
			cand = [f["value"] for f in fields if target and (f["value"] == target or f["value"].endswith("." + target))]
			if cand:
				field, conf, reason = cand[0], 0.9, "common synonym"
		if not field:
			best = difflib.get_close_matches(n, list(by_key), n=1, cutoff=0.82)
			if best:
				field, conf, reason = by_key[best[0]], 0.7, f"similar to '{best[0]}'"
		if field in used:
			field, conf, reason = None, 0.0, "already mapped to another column"
		if field:
			used.add(field)
		samples = [reader._jsonable(r[i]) for r in data[:5] if i < len(r) and r[i] is not None][:3]
		out.append({"index": i, "header": h, "field": field, "confidence": conf, "reason": reason, "samples": samples})
	return out


# ---------------------------------------------------------------- session lifecycle
def _hash(obj) -> str:
	return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _load_rows(session) -> tuple[list[str], list[list]]:
	f = files.owned_attachment(session.source_file)
	content = files.read_bytes(f)
	if files.sha256(content) != session.source_hash:
		raise ImportError_(_("The source file changed after mapping. Please start the import again."))
	loaded = reader.load(content, f.file_name, max_cells=2_000_000)
	sheet = next((s for s in loaded["sheets"] if s["name"] == session.sheet_name), None)
	if not sheet:
		raise ImportError_(_("Sheet not found."))
	headers, data = reader.table(sheet, header_row=cint(session.header_row) - 1)
	return headers, data


def owned_session(name: str):
	doc = frappe.get_doc(SESSION, name) if name and frappe.db.exists(SESSION, name) else None
	if not doc or doc.user != frappe.session.user:
		raise frappe.DoesNotExistError(_("Import session not found."))
	return doc


def start(file_name: str, conversation: str, doctype: str | None, sheet: str | None, settings) -> dict:
	f = files.owned_attachment(file_name, conversation)
	content = files.read_bytes(f)
	loaded = reader.load(content, f.file_name, max_cells=2_000_000)
	sheets = [s for s in loaded["sheets"] if s["rows"]]
	if not sheets:
		raise ImportError_(_("The file has no data."))
	target = next((s for s in sheets if s["name"] == sheet), sheets[0])
	h_idx = reader.detect_header(target["rows"])
	headers, data = reader.table(target, header_row=h_idx)
	max_rows = cint(settings.import_max_rows) or 5000
	if len(data) > max_rows:
		raise ImportError_(_("The sheet has {0} rows; the limit per import is {1}. Split the file and try again.").format(len(data), max_rows))
	info = {"file": f.file_name, "sheets": [s["name"] for s in sheets], "sheet": target["name"], "rows": len(data),
			"headers": headers, "sample": [dict(zip(headers, [reader._jsonable(v) for v in r])) for r in data[:3]]}
	if not doctype:
		scores = []
		for e in eligible_doctypes(settings):
			props = propose_mapping(e["doctype"], headers, data)
			scores.append((sum(p["confidence"] for p in props), e["doctype"]))
		info["suggested_doctypes"] = [d for s, d in sorted(scores, reverse=True)[:5] if s > 0]
		info["eligible_doctypes"] = [e["doctype"] for e in eligible_doctypes(settings)]
		info["next_step"] = "Ask the user which DocType to import into, then call start_import again with doctype."
		return info
	el = eligibility(doctype, settings)
	if not el["eligible"]:
		raise ImportError_(_("{0} cannot be imported: {1}").format(doctype, " ".join(el["reasons"])))
	mapping = propose_mapping(doctype, headers, data)
	tmpl = frappe.get_list("Chikku AI Import Template", filters={"reference_doctype": doctype,
																  "header_signature": _hash([_norm(h) for h in headers])},
						   fields=["name", "mapping_json", "defaults_json"], limit_page_length=1)
	defaults = {}
	if tmpl:
		saved = {m["header"]: m.get("field") for m in json.loads(tmpl[0].mapping_json or "[]")}
		for m in mapping:
			if m["header"] in saved:
				m["field"], m["confidence"], m["reason"] = saved[m["header"]], 1.0, "saved template"
		defaults = json.loads(tmpl[0].defaults_json or "{}")
	session = frappe.get_doc({
		"doctype": SESSION, "title": f"{doctype} from {f.file_name}"[:140], "status": "Mapping", "user": frappe.session.user,
		"conversation": conversation, "reference_doctype": doctype, "import_type": el["modes"][0],
		"source_file": f.name, "source_hash": files.sha256(content), "sheet_name": target["name"], "header_row": h_idx + 1,
		"columns_json": json.dumps([{"index": i, "header": h} for i, h in enumerate(headers)]),
		"mapping_json": json.dumps(mapping, default=str), "defaults_json": json.dumps(defaults), "total_rows": len(data),
	})
	session.insert(ignore_permissions=True)  # code-written record owned by the session user
	return {**info, "session": session.name, "doctype": doctype, "modes": el["modes"], "template_applied": bool(tmpl),
			"mapping_block": mapping_block(session)}


def mapping_block(session) -> dict:
	fields = importable_fields(session.reference_doctype)
	mapping = json.loads(session.mapping_json or "[]")
	mapped = {m["field"] for m in mapping if m.get("field")}
	defaults = json.loads(session.defaults_json or "{}")
	missing = [f for f in fields if f["reqd"] and not f["table"] and f["value"] not in mapped and f["value"] not in defaults
			   and f["value"] not in AUTO_FIELDS and not frappe.get_meta(session.reference_doctype).get_field(f["value"]).default]
	return {"type": "import_mapping", "session": session.name, "doctype": session.reference_doctype,
			"status": session.status, "import_type": session.import_type, "rows": session.total_rows, "sheet": session.sheet_name,
			"columns": mapping, "fields": fields, "defaults": defaults, "parent_key_column": session.parent_key_column,
			"missing_mandatory": [{"value": f["value"], "label": f["label"]} for f in missing],
			"modes": eligibility(session.reference_doctype)["modes"]}


def update_mapping(session_name: str, mapping: list, defaults: dict, import_type: str | None, parent_key_column: str | None):
	s = owned_session(session_name)
	if s.status not in ("Mapping", "Validated", "Validating"):
		raise ImportError_(_("This import can no longer be changed."))
	allowed = {f["value"]: f for f in importable_fields(s.reference_doctype)}
	headers = [c["header"] for c in json.loads(s.columns_json)]
	clean, used = [], set()
	for m in mapping or []:
		idx = cint(m.get("index"))
		if idx < 0 or idx >= len(headers):
			continue
		field = m.get("field") or None
		if field and field not in allowed:
			raise ImportError_(_("Field '{0}' cannot be imported into {1}.").format(cstr(field)[:60], s.reference_doctype))
		if field and field in used:
			raise ImportError_(_("Field '{0}' is mapped to more than one column.").format(field))
		if field:
			used.add(field)
		clean.append({"index": idx, "header": headers[idx], "field": field, "confidence": 1.0 if field else 0,
					  "reason": "set by user", "samples": m.get("samples") or []})
	clean_defaults = {}
	for k, v in (defaults or {}).items():
		if k not in allowed or allowed[k]["table"]:
			raise ImportError_(_("Default for '{0}' is not allowed.").format(cstr(k)[:60]))
		if v not in (None, ""):
			clean_defaults[k] = cstr(v)[:140]
	if import_type and import_type not in eligibility(s.reference_doctype)["modes"]:
		raise ImportError_(_("Import type not allowed."))
	if parent_key_column and parent_key_column not in headers:
		raise ImportError_(_("Parent key column not found."))
	if any((m["field"] or "").count(".") for m in clean) and not parent_key_column:
		pass  # each row then becomes one record with one child row
	s.update({"mapping_json": json.dumps(clean), "defaults_json": json.dumps(clean_defaults),
			  "import_type": import_type or s.import_type, "parent_key_column": parent_key_column or None,
			  "status": "Mapping", "validation_json": None, "valid_rows": 0, "invalid_rows": 0, "duplicate_rows": 0,
			  "mapping_hash": None})
	s.save(ignore_permissions=True)
	return mapping_block(s)


# ---------------------------------------------------------------- validation
def _convert(value, df_type, options):
	if value is None or value == "":
		return None, None
	try:
		if df_type in ("Int",):
			return cint(flt(value)), None
		if df_type in ("Float", "Currency", "Percent"):
			if isinstance(value, str):
				value = value.replace(",", "")
			return flt(value), None
		if df_type == "Check":
			return (1 if str(value).strip().lower() in ("1", "yes", "true", "y") else 0), None
		if df_type in ("Date",):
			return str(getdate(value)), None
		if df_type == "Datetime":
			return str(frappe.utils.get_datetime(value)), None
		if df_type == "Select":
			opts = [o for o in cstr(options).split("\n") if o]
			if opts and str(value) not in opts:
				match = next((o for o in opts if o.lower() == str(value).strip().lower()), None)
				if match:
					return match, None
				return None, f"'{value}' is not one of: {', '.join(opts[:8])}"
			return str(value), None
		return cstr(value).strip(), None
	except Exception:
		return None, f"'{value}' is not a valid {df_type}"


def build_records(session, headers, data):
	"""Group rows into records (parent + child rows) and convert types. Returns (records, row_errors)."""
	fields = {f["value"]: f for f in importable_fields(session.reference_doctype)}
	mapping = [m for m in json.loads(session.mapping_json or "[]") if m.get("field")]
	defaults = json.loads(session.defaults_json or "{}")
	key_idx = headers.index(session.parent_key_column) if session.parent_key_column in headers else None
	only = set(json.loads(session.row_filter_json or "[]")) if session.get("row_filter_json") else None
	records, errors, current, current_key = [], [], None, object()
	for r_i, row in enumerate(data):
		row_no = r_i + cint(session.header_row) + 1
		if only is not None and row_no not in only:
			continue
		parent, child = {}, {}
		for m in mapping:
			f = fields[m["field"]]
			v, err = _convert(row[m["index"]] if m["index"] < len(row) else None, f["fieldtype"], f["options"])
			if err:
				errors.append({"row": row_no, "field": f["label"], "message": err})
			if v is None:
				continue
			if f["table"]:
				child.setdefault(f["table"], {})[m["field"].split(".", 1)[1]] = v
			else:
				parent[m["field"]] = v
		key = row[key_idx] if key_idx is not None else object()
		if key_idx is not None and current is not None and key == current_key:
			for t, c in child.items():
				current["children"].setdefault(t, []).append(c)
			current["rows"].append(row_no)
			continue
		for k, v in defaults.items():
			parent.setdefault(k, _convert(v, fields[k]["fieldtype"], fields[k]["options"])[0])
		current = {"rows": [row_no], "doc": parent, "children": {t: [c] for t, c in child.items()}}
		current_key = key
		records.append(current)
	return records, errors


def validate(session_name: str, settings) -> dict:
	s = owned_session(session_name)
	s.db_set("status", "Validating")
	headers, data = _load_rows(s)
	doctype = s.reference_doctype
	meta = frappe.get_meta(doctype)
	fields = {f["value"]: f for f in importable_fields(doctype)}
	records, conv_errors = build_records(s, headers, data)
	errors = list(conv_errors)
	row_status = {}
	for e in conv_errors:
		row_status[e["row"]] = "invalid"

	def err(rec, field, msg, kind="invalid"):
		for rn in rec["rows"][:1]:
			errors.append({"row": rn, "field": field, "message": msg})
			if row_status.get(rn) != "invalid":
				row_status[rn] = kind

	# mandatory
	reqd = [f for f in fields.values() if f["reqd"] and not f["table"] and f["value"] not in AUTO_FIELDS
			and not (meta.get_field(f["value"]) and meta.get_field(f["value"]).default)]
	insert_mode = s.import_type == "Insert New Records"
	for rec in records:
		if insert_mode:
			for f in reqd:
				if rec["doc"].get(f["value"]) in (None, ""):
					err(rec, f["label"], "is mandatory but empty")
	# links (permission-aware existence, batched per target DocType)
	link_values = {}
	for rec in records:
		for k, v in rec["doc"].items():
			if fields[k]["fieldtype"] == "Link" and v:
				link_values.setdefault(fields[k]["options"], set()).add(v)
		for t, rows in rec["children"].items():
			for c in rows:
				for k, v in c.items():
					f = fields.get(f"{t}.{k}")
					if f and f["fieldtype"] == "Link" and v:
						link_values.setdefault(f["options"], set()).add(v)
	found = {}
	for ldt, values in link_values.items():
		values = list(values)
		ok = set()
		if frappe.has_permission(ldt, "read") or frappe.has_permission(ldt, "select"):
			for i in range(0, len(values), 400):
				ok |= set(frappe.get_list(ldt, filters={"name": ["in", values[i:i + 400]]}, pluck="name",
										  limit_page_length=400))
		found[ldt] = ok
	for rec in records:
		for k, v in rec["doc"].items():
			f = fields[k]
			if f["fieldtype"] == "Link" and v and v not in found.get(f["options"], set()):
				err(rec, f["label"], f"{f['options']} '{v}' not found or not accessible")
			if k == "company" and v and not frappe.has_permission("Company", "read", v):
				err(rec, f["label"], "you don't have access to this company")
		for t, rows in rec["children"].items():
			for c in rows:
				for k, v in c.items():
					f = fields.get(f"{t}.{k}")
					if f and f["fieldtype"] == "Link" and v and v not in found.get(f["options"], set()):
						err(rec, f["label"], f"{f['options']} '{v}' not found or not accessible")
	# duplicates in file + existing records
	key_fields = [df.fieldname for df in meta.fields if df.unique and df.fieldname in fields]
	autoname = (meta.autoname or "")
	if autoname.startswith("field:") and autoname[6:] in fields:
		key_fields.insert(0, autoname[6:])
	if "name" in {m["field"] for m in json.loads(s.mapping_json or "[]")}:
		key_fields.insert(0, "name")
	title = meta.get_title_field()
	if title in fields and title != "name" and title not in key_fields:
		key_fields.append(title)
	seen = {}
	for rec in records:
		for kf in key_fields[:3]:
			v = rec["doc"].get(kf)
			if v in (None, ""):
				continue
			sig = (kf, str(v).strip().lower())
			if sig in seen:
				err(rec, fields.get(kf, {}).get("label", kf), f"duplicate of row {seen[sig]} in this file", "duplicate")
			else:
				seen[sig] = rec["rows"][0]
	for kf in key_fields[:3]:
		vals = list({rec["doc"].get(kf) for rec in records if rec["doc"].get(kf)})
		existing = set()
		for i in range(0, len(vals), 400):
			if frappe.has_permission(doctype, "read"):
				existing |= {str(x) for x in frappe.get_list(doctype, filters={kf: ["in", vals[i:i + 400]]}, pluck=kf,
															 limit_page_length=400)}
		for rec in records:
			v = rec["doc"].get(kf)
			if v and insert_mode and str(v) in existing:
				err(rec, fields.get(kf, {}).get("label", kf), f"a {doctype} with {kf} '{v}' already exists", "duplicate")
			if v and not insert_mode and kf == "name" and str(v) not in existing:
				err(rec, "ID", "record to update not found or not accessible")
	# ERPNext business validations: test-insert the first N clean records inside a savepoint, then roll back
	dry = cint(settings.import_dry_run_rows) or 0
	tested = 0
	if insert_mode and dry:
		for rec in records:
			if tested >= dry:
				break
			if any(row_status.get(rn) for rn in rec["rows"]):
				continue
			tested += 1
			doc = {"doctype": doctype, **rec["doc"]}
			for t, rows in rec["children"].items():
				doc[t] = rows
			frappe.db.savepoint("chikku_import_dry")
			try:
				frappe.flags.mute_emails = True
				d = frappe.get_doc(doc)
				d.flags.chikku_dry_run = True
				d.insert()
			except Exception as e:
				msg = frappe.utils.strip_html(cstr(e)) or type(e).__name__
				err(rec, "SaarthiERP validation", msg[:300])
			finally:
				frappe.db.rollback(save_point="chikku_import_dry")
				frappe.flags.mute_emails = False
				frappe.clear_messages()
	total_rows = len(json.loads(s.row_filter_json)) if s.row_filter_json else len(data)
	invalid = sum(1 for v in row_status.values() if v == "invalid")
	dups = sum(1 for v in row_status.values() if v == "duplicate")
	bad_rows = {rn for rn in row_status}
	valid_records = [rec for rec in records if not any(rn in bad_rows for rn in rec["rows"])]
	preview = {
		"total_rows": total_rows, "records": len(records), "valid_records": len(valid_records),
		"valid_rows": sum(len(r["rows"]) for r in valid_records), "invalid_rows": invalid, "duplicate_rows": dups,
		"errors": sorted(errors, key=lambda e: e["row"])[:300], "error_count": len(errors),
		"dry_run_tested": tested,
		"sample": [{**r["doc"], **{t: f"{len(c)} row(s)" for t, c in r["children"].items()}} for r in valid_records[:5]],
		"warnings": ([f"{doctype} is submittable: imported records stay as Drafts and are not submitted."] if meta.is_submittable else [])
		+ (["Native import commits record by record: if some rows fail, the others are still imported (no all-or-nothing)."]),
		"estimated_seconds": max(5, int(len(valid_records) * 0.4)),
		"valid_row_numbers": [rn for r in valid_records for rn in r["rows"]],
	}
	s.reload()
	s.update({"status": "Validated", "valid_rows": preview["valid_rows"], "invalid_rows": invalid, "duplicate_rows": dups,
			  "validation_json": json.dumps(preview, default=str)[:1_000_000],
			  "mapping_hash": _hash([s.mapping_json, s.defaults_json, s.import_type, s.parent_key_column])})
	s.save(ignore_permissions=True)
	return preview_block(s)


def preview_block(s) -> dict:
	v = json.loads(s.validation_json or "{}")
	return {"type": "import_preview", "session": s.name, "doctype": s.reference_doctype, "status": s.status,
			"import_type": s.import_type, **{k: v.get(k) for k in ("total_rows", "records", "valid_records", "valid_rows",
																	"invalid_rows", "duplicate_rows", "errors", "error_count",
																	"sample", "warnings", "estimated_seconds", "dry_run_tested")},
			"mapping": [m for m in json.loads(s.mapping_json or "[]") if m.get("field")],
			"defaults": json.loads(s.defaults_json or "{}")}


# ---------------------------------------------------------------- confirmation + execution
IMPORT_ACTION = "import.execute"


def request_confirmation(session_name: str, settings) -> dict:
	from frappe_ai_assistant.frappe_ai_assistant.security.tokens import create_action_request

	s = owned_session(session_name)
	if s.status != "Validated":
		raise ImportError_(_("Validate the import before confirming."))
	v = json.loads(s.validation_json or "{}")
	if not v.get("valid_records"):
		raise ImportError_(_("There are no valid records to import."))
	params = {"session": s.name, "doctype": s.reference_doctype, "import_type": s.import_type, "source_hash": s.source_hash,
			  "mapping_hash": s.mapping_hash, "rows": v["valid_row_numbers"]}
	summary = f"Import {v['valid_records']} {s.reference_doctype} record(s) from {s.title}"
	name, token = create_action_request(action=IMPORT_ACTION, params=params,
										preview={"doctype": s.reference_doctype, "records": v["valid_records"]},
										summary=summary, target_doctype=s.reference_doctype, conversation=s.conversation,
										message=None, expiry_minutes=settings.action_expiry_minutes or 10)
	return {"action_request": name, "token": token, "summary": summary}


def revalidate_for_execution(settings, params: dict):
	s = owned_session(params["session"])
	if s.status != "Validated":
		raise ImportError_(_("This import is not ready (status {0}).").format(s.status))
	if s.source_hash != params["source_hash"] or s.mapping_hash != params["mapping_hash"]:
		raise ImportError_(_("The file or mapping changed after validation. Validate again."))
	if s.reference_doctype != params["doctype"] or s.import_type != params["import_type"]:
		raise ImportError_(_("Import parameters changed. Validate again."))
	el = eligibility(s.reference_doctype, settings)
	if not el["eligible"] or s.import_type not in el["modes"]:
		raise ImportError_(_("You are no longer allowed to import into {0}.").format(s.reference_doctype))
	return s


def execute_action(settings, params: dict):
	"""Action handler (called by api.actions.confirm after the token is claimed)."""
	s = revalidate_for_execution(settings, params)
	s.db_set("status", "Confirmed")
	frappe.enqueue("frappe_ai_assistant.frappe_ai_assistant.imports.studio.run_import_job", queue="long", timeout=3600,
				   job_id=f"chikku_import::{s.name}", deduplicate=True, enqueue_after_commit=True,
				   session=s.name, rows=params["rows"])
	return s


def native_csv(s, headers, data, rows: list[int]) -> bytes:
	selected = set(rows)
	s = frappe._dict(s.as_dict())
	s.row_filter_json = json.dumps(sorted(selected))
	records, _errors = build_records(s, headers, data)
	parent_cols = sorted({k for r in records for k in r["doc"]})
	child_cols = sorted({f"{t}.{k}" for r in records for t, rs in r["children"].items() for c in rs for k in c})
	out = io.StringIO()
	w = csv.writer(out)
	w.writerow(parent_cols + child_cols)
	for r in records:
		children = [(t, c) for t, rs in r["children"].items() for c in rs] or [(None, {})]
		for j, (t, c) in enumerate(children):
			line = [r["doc"].get(k, "") if j == 0 else "" for k in parent_cols]
			line += [c.get(k.split(".", 1)[1], "") if t and k.startswith(t + ".") else "" for k in child_cols]
			w.writerow(["" if v is None else v for v in line])
	return out.getvalue().encode("utf-8")


def run_import_job(session: str, rows: list[int]):
	from frappe.core.doctype.data_import.data_import import start_import as native_start

	s = frappe.get_doc(SESSION, session)
	if s.user != frappe.session.user:
		from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit

		audit("job_identity_mismatch", "Critical", details={"import_session": session})
		return
	publish = lambda payload: frappe.publish_realtime(REALTIME_EVENT, {"type": "import_status", "session": session,  # noqa: E731
																		 **payload}, user=s.user)
	try:
		s.db_set("status", "Importing")
		frappe.db.commit()
		publish({"status": "Importing"})
		headers, data = _load_rows(s)
		content = native_csv(s, headers, data, rows)
		di = frappe.get_doc({"doctype": "Data Import", "reference_doctype": s.reference_doctype,
							 "import_type": s.import_type, "submit_after_import": 0, "mute_emails": 1})
		di.insert()  # requires the user's own Data Import permission
		f = files.save_output(content, f"chikku_import_{s.name}.csv", "Data Import", di.name)
		di.reload()
		di.import_file = f["url"]
		di.submit_after_import = 0
		di.save()
		s.db_set("data_import", di.name)
		frappe.db.commit()
		publish({"status": "Importing", "data_import": di.name})
		native_start(di.name)  # native engine: per-record insert as this user, per-record commit
		di.reload()
		logs = frappe.get_all("Data Import Log", filters={"data_import": di.name},  # native log of this user's import
							  fields=["success", "row_indexes", "messages", "docname"])
		ok = sum(1 for log in logs if log.success)
		bad = [log for log in logs if not log.success]
		status = "Completed" if not bad and ok else ("Partially Completed" if ok else "Failed")
		error_file = None
		if bad:
			error_file = _error_report(s, bad)
		s.reload()
		s.update({"status": status, "success_count": ok, "failed_count": len(bad), "error_file": (error_file or {}).get("file")})
		s.save(ignore_permissions=True)
		frappe.db.commit()
		publish({"status": status, "success": ok, "failed": len(bad), "data_import": di.name,
				 "error_file": (error_file or {}).get("url")})
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Chikku AI import failed")
		frappe.db.set_value(SESSION, session, {"status": "Failed", "error": frappe.utils.strip_html(cstr(e))[:500]})
		frappe.db.commit()
		publish({"status": "Failed", "error": frappe.utils.strip_html(cstr(e))[:300]})


def _error_report(s, bad_logs) -> dict:
	import xlsxwriter

	buf = io.BytesIO()
	wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False})
	ws = wb.add_worksheet("Failed Rows")
	head = wb.add_format({"bold": True, "bg_color": "#fee2e2"})
	ws.write_row(0, 0, ["Import Row(s)", "Error"], head)
	for i, log in enumerate(bad_logs, start=1):
		try:
			msgs = json.loads(log.messages or "[]")
			text = "; ".join(frappe.utils.strip_html(json.loads(m).get("message", "") if isinstance(m, str) else str(m)) for m in msgs)
		except Exception:
			text = frappe.utils.strip_html(cstr(log.messages))
		ws.write_string(i, 0, cstr(log.row_indexes))
		ws.write_string(i, 1, text[:2000] or "Unknown error")
	ws.set_column(0, 0, 16)
	ws.set_column(1, 1, 100)
	wb.close()
	return files.save_output(buf.getvalue(), f"import_errors_{s.name}.xlsx", SESSION, s.name)


def failed_row_numbers(s) -> list[int]:
	if not s.data_import:
		return []
	logs = frappe.get_all("Data Import Log", filters={"data_import": s.data_import, "success": 0}, pluck="row_indexes")
	native_rows = set()
	for ri in logs:
		try:
			native_rows |= set(json.loads(ri or "[]"))
		except ValueError:
			pass
	return sorted(native_rows)


def retry_failed(session_name: str) -> dict:
	"""New session containing only the records that failed. Successful records are never re-sent."""
	s = owned_session(session_name)
	if s.status not in ("Partially Completed", "Failed") or not s.data_import:
		raise ImportError_(_("Only failed or partially completed imports can be retried."))
	v = json.loads(s.validation_json or "{}")
	imported_rows = v.get("valid_row_numbers") or []
	native_failed = failed_row_numbers(s)  # native row numbers refer to the generated file (header = row 1)
	# map native file rows back to source rows (generated file keeps record order; 1 row per child)
	retry_rows = [imported_rows[i - 2] for i in native_failed if 0 <= i - 2 < len(imported_rows)]
	new = frappe.copy_doc(s)
	new.update({"status": "Mapping", "retry_of": s.name, "data_import": None, "success_count": 0, "failed_count": 0,
				"error_file": None, "validation_json": None, "row_filter_json": json.dumps(retry_rows),
				"title": f"Retry: {s.title}"[:140], "total_rows": len(retry_rows),
				"valid_rows": 0, "invalid_rows": 0, "duplicate_rows": 0, "mapping_hash": None, "error": None})
	new.insert(ignore_permissions=True)
	return {"session": new.name, "rows_to_retry": len(retry_rows), "mapping_block": mapping_block(new)}


def guard_data_import(doc, method=None):
	"""doc_events hook: a Data Import created by Chikku AI can never be set to submit after import."""
	if doc.submit_after_import and frappe.db.exists(SESSION, {"data_import": doc.name}):
		frappe.throw(_("Imports started from Chikku AI cannot submit documents. Submit them through the normal workflow."))
