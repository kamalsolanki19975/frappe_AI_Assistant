# Deliverable 10 — Excel and Import Specification

---

# Part A — Excel Intelligence

## A1. Library comparison (versions installed on this bench)

| Criterion | openpyxl 3.1.5 (MIT) | XlsxWriter 3.2.9 (BSD-2) | xlrd 2.0.2 | Frappe `utils/xlsxutils.make_xlsx` |
|---|---|---|---|---|
| Read xlsx | ✓ (`read_only=True` streaming) | ✗ | xls only | ✗ |
| Write xlsx | ✓ | ✓ | ✗ | ✓ (basic, uses openpyxl) |
| Modify existing | ✓ | ✗ (write-only) | ✗ | ✗ |
| Formulas | stores strings; **does not calculate**; `data_only=True` returns cached values | writes formulas (no calc; Excel recalcs on open) | — | — |
| Charts | basic, and **lost when loading+saving a workbook that had them** (images, pivots, slicers likewise) | rich (bar/line/pie/scatter/combo, sheets) | — | ✗ |
| Conditional formatting / data validation | ✓ | ✓ (more complete) | — | ✗ |
| Freeze panes, autofilter, tables | ✓ | ✓ | — | partial |
| Large files | read_only mode ~constant memory; write_only mode | `constant_memory` mode | | |
| XML attack hardening | uses `defusedxml` **if installed** (it is **not**) | n/a (writer) | | |
| Already a frappe dependency | ✓ | ✓ | ✓ (frappe) | ✓ |
| Illegal-char handling | — | — | — | `ILLEGAL_CHARACTERS_RE` [V `xlsxutils.py:24`] |

**Decision:**
- **Read/analyse:** openpyxl `load_workbook(path, read_only=True, data_only=True, keep_links=False)`; CSV via `csv` + `chardet`; `.xls` via xlrd (read-only, legacy).
- **Generate:** XlsxWriter (professional formatting, charts, CF, validation, freeze panes, tables, `constant_memory` for large exports).
- **Modify:** default = **rebuild**: read source with openpyxl, apply operations in memory, write a **new** workbook with XlsxWriter, keeping the original file untouched. In-place openpyxl edit (`load_workbook(..., keep_vba=False)`) only when the inspector confirms the source contains no charts, drawings, pivot tables or images (detected via zip part names `xl/charts/`, `xl/drawings/`, `xl/pivotTables/`, `xl/media/`); otherwise the user is warned ("charts in the original will be recreated / not preserved").
- Add **`defusedxml`** (tiny, pure Python, PSF licence) as the one recommended new dependency → openpyxl auto-enables it.

## A2. Workbook generation spec

```json
{
  "filename": "Sales_Oct_vs_Sep_2026.xlsx",
  "heading_lang": "en+hi",
  "sheets": [
    {"title": "Summary", "type": "dashboard",
     "kpis": [{"label": "Sales Oct", "source": "tr_12.total"}],
     "charts": [{"type": "column", "source": "tr_12", "x": "month", "y": ["amount"]}]},
    {"title": "Detail", "type": "table", "source": "tr_13",
     "columns": [{"field": "customer", "label": "Customer", "width": 30},
                 {"field": "amount", "label": "Amount", "format": "inr"}],
     "freeze": "A2", "autofilter": true,
     "conditional": [{"column": "amount", "rule": "top10", "format": "green"}],
     "totals_row": {"amount": "SUM"},
     "validation": []}
  ]
}
```

- `source` = cached server tool result id; numbers never come from model text.
- Formulas generated only from a fixed set (`SUM`, `AVERAGE`, `COUNT`, `SUBTOTAL`, `IF`, simple arithmetic between same-row cells) built by code, not free-form model strings.
- Indian number format `#,##,##0.00` and dates as real Excel dates (`dd-mm-yyyy` display, from System Settings).
- Output: private `File` attached to the `Chikku AI File Job`; download via permission-checked endpoint.

## A3. Analysis spec

Profile per sheet: dimensions, header row detection (first row whose non-empty ratio ≥ 0.6 and mostly strings, with up to 10 rows scanned for title rows/merged headers), column types (number/date/text/bool/mixed with % mix), nulls, distinct counts, duplicates (full-row and on candidate key columns), min/max/mean/sum for numerics, outliers (IQR), suspicious values (leading/trailing spaces, numbers stored as text, mixed date formats). Natural-language questions are answered through `files.query_table` (filter/group/sort/pivot spec executed in Python), so numbers stay deterministic.

Caps: 200,000 cells read for profiling; beyond that, sample + warn; hard reject > 2,000,000 cells.

## A4. Modification operations (R2)

`add_sheet`, `rename_sheet`, `delete_sheet` (requires explicit user confirmation in the turn), `update_cells(range, values)`, `add_column(formula_template)`, `format(range, style)`, `add_chart`, `merge_datasets(att_a, att_b, keys, how)`, `clean(trim/case/dedupe/standardise_dates/numbers_from_text)`, `compare(att_a, att_b, keys)` → diff sheet with added/removed/changed rows highlighted. Each produces version n+1 as a new File; versions listed in the File Job.

## A5. Workbook security

| Threat | Control |
|---|---|
| Oversized file | max 15 MB upload (setting); Frappe global max 10 MB unless `max_file_size` raised: **align these**. |
| ZIP bomb / XML bloat | Before openpyxl: open with `zipfile`, sum `file_size` of members ≤ 100 MB, ratio per member ≤ 100:1, member count ≤ 1,000, reject nested archives; then parse inside the resource-limited subprocess (RLIMIT_AS 512 MB, 60 s). |
| XXE / billion laughs | `defusedxml` (recommended add); DOCX parsing via lxml with `resolve_entities=False, no_network=True, huge_tree=False`. |
| Macros | reject `.xlsm .xltm .xlsb .xlam` and any xlsx containing `xl/vbaProject.bin`. Never `keep_vba`. |
| External links / DDE | `keep_links=False`; flag `xl/externalLinks/` presence; formulas never evaluated. |
| Formula injection (CSV/XLSX export) | In generated files, any **string** cell starting with `= + - @ \t \r` written via `write_string` (XlsxWriter treats it as text) and, for CSV exports, prefixed with `'`. ERP data and user text are never written with `write_formula`. |
| Prompt injection inside cells | Cell text is passed to the model only inside `<untrusted_document>` wrappers and only as samples (≤ 20 rows) or tool results; instructions inside are ignored by policy and flagged by a heuristic detector. |
| Download access | files private; `files.download` checks the File is attached to a Chikku record owned by the caller (or Manager), streams with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`. |
| Temp files | `tempfile.TemporaryDirectory` inside `sites/<site>/private/chikku_tmp`, removed in `finally`; daily purge job for leftovers > 24 h. |
| Retention | generated/uploaded files purged after `retention_files_days` (default 30) unless the user pins them. |

---

# Part B — Conversational Data Import Studio

## B1. Principle

**Chikku is the front-end (understanding, mapping, transformation, explanation); the native Frappe Data Import is the execution engine.** Verified reasons: native import already handles link validation, select options, date parsing, parent/child payloads, tree parents, per-record commit, logs, realtime progress, errored-row export and stop. A competing importer would duplicate ~2,300 lines of maintained core code (`importer.py`).

## B2. Eligibility engine (dynamic, no hardcoded DocType list)

```python
def eligible_doctypes(user) -> list[EligibleDoctype]:
    for dt in DocType where allow_import=1 and istable=0 and issingle=0 and is_virtual=0:
        skip if dt in frappe BLOCKED_DOCTYPES (data_import.py:23)
        skip if dt in CHIKKU_IMPORT_DENYLIST      # User, Role, Workflow*, Server/Client Script,
                                                   # Custom Field, Property Setter, DocPerm, User Permission,
                                                   # GL Entry, Stock Ledger Entry, Payment Ledger Entry,
                                                   # *Settings, Chikku AI *, Access Control, …
        perms: import = has_permission(dt,"import"), create = has_permission(dt,"create"),
               write  = has_permission(dt,"write")
        skip unless import and (create or write)
        mode: insert if create; update if write; upsert if both
        submittable flag → "imported as Draft only"
        yield {doctype, label, module, modes, is_submittable, has_child_tables, naming_rule}
```

Today, 220 DocTypes pass the structural filter [V]; the per-user set will be much smaller. Results are cached per user for 10 min (cleared on Role/DocPerm change).

## B3. Metadata model for mapping

From `frappe.get_meta(dt)` (includes Custom Fields and Property Setters): importable fields = not in `no_value_fields`/`table_fields` handling, not `read_only` unless `fetch_from`-less and allowed by native import, not `hidden` system fields; `reqd` (after property setters); `fieldtype`; `options` (Link target / Select options); `unique`; child tables → same for child meta. For consistency, reuse **`build_fields_dict_for_column_matching`** (`importer.py:2109`), so Chikku's mapping matches exactly what native import will accept (column header strings like `Item Code (Items)`).

## B4. Mapping algorithm

1. Header normalisation (lowercase, strip, remove punctuation, transliterate Devanagari headers, split camel/snake).
2. Exact match on label/fieldname (incl. native header format) → confidence 1.0.
3. Synonym table (editable; seeded with garment-domain terms: "qty/pcs/nos" → qty, "hsn" → gst_hsn_code, "style no" → item_code?) → 0.9.
4. Fuzzy (token-set ratio via `difflib`, no new deps) ≥ 0.85 → 0.7.
5. Value-based check on samples: Link fields validated against `get_list` existence; Select vs options; dates parse rate; numeric parse rate. Raises or lowers confidence.
6. LLM (`fast` slot) only for **remaining unmapped columns**, given header + 5 sample values + candidate field list; output constrained to schema `{column: fieldname|null, reason}`; never invents fields (validated against candidate list).
7. Mandatory coverage report: unmapped mandatory fields → ask the user for a constant value or column.

## B5. Conversational flow (maps to the 25 required steps)

| Steps | Component |
|---|---|
| 1–4 receive, parse, sheets, headers | `files.inspect` |
| 5 understand samples | profile + `fast` slot summary |
| 6 recommend DocType | eligible list ∩ header similarity score + user words |
| 7 metadata | B3 |
| 8–10 mapping + manual correction | B4 + mapping grid UI |
| 11 mandatory | coverage report |
| 12–14 type/link/select validation | Chikku pre-validation **and** native preview |
| 15 custom fields | come from meta automatically |
| 16 parent-child | B6 |
| 17 duplicates | B7 |
| 18–20 preview + row-level validation + dry run | native `get_preview_from_template` on the generated native file + `ImportFile.get_warnings()`; for deeper dry-run see B8 |
| 21 confirm | Action Request token (`import.start`) |
| 22 execute | native `form_start_import` (as user) |
| 23 progress | native `data_import_refresh`/progress realtime events relayed to the panel |
| 24 reports | native `Data Import Log` → success/failure table + errored XLSX |
| 25 retry | `download_errored_template` → new session prefilled with the same mapping |

## B6. Parent-child and multi-sheet

- Native format: one sheet; parent columns + child columns labelled `Field (Child Table Label)`; first row of each record carries parent values, continuation rows leave parent columns blank (`Row.parse_doc`).
- Chikku transformer handles:
  - **Flat sheet with repeated parent values** (most common for Sales Orders): group by user-confirmed parent key (e.g. "Order Ref"), emit native layout.
  - **Two sheets** (Header + Lines): join on key column; orphan lines and headers without lines reported.
  - Multiple child tables (e.g. items + taxes): separate column groups.
- Parent key identification: suggested by uniqueness analysis (column with repeated values whose other "parent-like" columns are constant within each group).
- Generated native file stored as private File and linked on the session, so it can be audited.

## B7. Duplicates and idempotency

- Within file: duplicate keys flagged (configurable key: name / unique fields / user-chosen).
- Against DB: for Insert mode, `get_list` existence check on unique/natural keys (e.g. `item_code`, `supplier_name`, `bill_no + supplier`) → rows marked "exists" → user chooses skip / switch to update (if write permission).
- Idempotent re-run: the session records the import's success rows (`Data Import Log.docname`); retry uses only errored rows, so already-imported records cannot be inserted twice.
- One active import per session (job_id = native `data_import||<name>`); double-click protection via the Action Request token (single-use).

## B8. Dry-run

Native preview validates structure, links, selects and dates, **but not controller validations** (e.g. ERPNext `validate()` business rules). Deep dry-run option (R2.5, POC): run the first *N* (≤ 20) payloads through `doc.insert()` inside a transaction with `frappe.db.savepoint` → rollback, catching validation messages. Risks: controllers that enqueue jobs, send emails or commit internally. Therefore: run it with `frappe.flags.mute_emails`, `in_import` set, and only for DocTypes on an allow-list after testing. Default remains native preview only.

## B9. Guaranteeing no submission (`submit_after_import`)

Defence in depth:
1. Chikku creates the Data Import with `submit_after_import=0` explicitly.
2. `doc_events["Data Import"]["validate"]` guard (in amba_garments, no core change): if the Data Import is linked from a `Chikku AI Import Session` (lookup by name) and `submit_after_import` is truthy → `frappe.throw`. This blocks anyone later ticking the box in the native form.
3. Immediately before `form_start_import`, the service reloads the Data Import and asserts `submit_after_import == 0`.
4. Post-import verification: sample `get_list(target, {"name": ["in", imported]}, ["docstatus"])` → all must be 0; any 1 → Audit Log severity *critical*.

## B10. Permission model options for Data Import (decision required: Q4)

Data Import is **System Manager-only** by default [V].

| Option | Description | Security | Change needed |
|---|---|---|---|
| **A (default)** | Conversational import only for users who already can create Data Import (System Manager) | strongest | none |
| B | Add Role Permission (via Role Permission Manager → Custom DocPerm, *not a core file change*) for `Chikku AI Importer` on Data Import: create/read/write **if_owner** | users still need `import` permission on the target DocType (checked by native `validate_doctype`) | configuration only |
| C | Chikku creates Data Import with `ignore_permissions` on behalf of users with target `import` perm | weakest: bypasses DocType perms on Data Import | **not recommended** |

Recommendation: **B**, rolled out to named users only.

## B11. Developer-mode caveat

`run_now = frappe.in_test or frappe.conf.developer_mode` (`data_import.py:198`): on `ambatest.local` (developer_mode=1) imports run **synchronously** inside the calling process. Chikku must call `start_import` from an RQ job (not from a web request) so a large import cannot pin a gunicorn worker for minutes on this site.

## B12. Reliability matrix

| Concern | Mechanism |
|---|---|
| Batch processing | native `data_import_batch_size` (default 1000) |
| Partial success | native per-record commit; session shows Partially Done |
| Cancellation | native `stop_data_import` |
| Progress | native realtime events |
| Session ownership | session `user`; Data Import `owner`; both checked |
| Audit | Audit Log on start/finish with counts; native logs keep per-row results |
| Retention | Chikku sessions 180 d; native Data Import records follow site policy (169 exist today) |
| Failed-row export | native `download_errored_template` (XLSX) |

## B13. Interaction with the existing Invoice Import Tool

`amba_garments` already has a custom **Invoice Import Tool** for Sales Invoices with company-specific logic (rates, tax templates, naming). For Sales Invoice imports, Chikku should *route to this tool*: create an Invoice Import Tool draft with mapped rows and open it, instead of the native Data Import. This preserves Amba-specific rules (open question Q3).
