# Deliverable 1 — Existing Environment Discovery Report

Inspection date: 2026-11-03. Method: read-only file inspection, `git` metadata, `pip list`, and one read-only Python session (`frappe.connect()` + `count`/`get_all`/`information_schema` queries). Nothing was installed, migrated, restarted or edited.

Legend: **[V]** verified on this server · **[D]** taken from official docs/web, not verified locally · **[A]** assumption.

---

## 1. Server and Bench

| Item | Value | Evidence |
|---|---|---|
| Bench path | `/home/frappe/frappe-bench` | [V] |
| Site | `ambatest.local` (only site, default site) | [V] `sites/` |
| Site mode | **`developer_mode: 1`** → this is a test/staging site, not production | [V] `site_config.json` |
| Frappe | **16.52.0** (branch `version-16`, commit `4515014c87`, 2026-10-14) | [V] `frappe/__init__.py:58` |
| ERPNext | **16.51.1** (`version-16`) | [V] `erpnext/__init__.py:10` |
| Other apps on site | india_compliance (16.8.x), hrms (16.15.x), lending (16.3.x), amba_garments, saarthi_home | [V] `site_config.installed_apps` |
| Apps on disk but **not installed** | `jarvis` (Aerele, AGPL-3.0, v16.5.1) | [V] |
| Python | 3.14.6 | [V] |
| Node | 18.19.1 | [V] |
| MariaDB | **10.11.14** (no `VECTOR` type; that needs 11.7+/11.8 LTS) | [V] `select version()` |
| Redis | 7.0.15 (plain redis-server, no RediSearch/vector module) | [V] |
| Process manager | supervisor: gunicorn **3 workers, timeout 120s**, 1 scheduler, 1 `short,default` worker, 1 `long,default,short` worker, socketio | [V] `config/supervisor.conf` |
| **CPU / RAM** | **1 vCPU, 1.9 GiB RAM, only ~111 MiB free, 1.4 GiB of 4 GiB swap in use** | [V] `nproc`, `free -h` |
| Disk | 48 GB, 65 % used (17 GB free) | [V] |
| DB size | ~1.19 GB | [V] |
| `server_script_enabled` | 1 | [V] `common_site_config.json` |

> **Critical infrastructure finding.** The server is already memory-starved. Self-hosted models, local OCR engines (PaddleOCR), local speech models and a vector database are **not viable on this machine**. Even the API-based assistant adds long-running jobs and socket traffic. Before production, plan for at least **4 vCPU / 8 GB RAM** and one additional dedicated RQ worker for AI jobs (see Deliverable 14).

## 2. Site data scale (read-only counts)

| DocType | Count | DocType | Count |
|---|---|---|---|
| User | 97 (5 enabled System Users) | Item | 44,300 |
| Company | 2 — *Amba Garments Limited*, *Radhe Krishna Clothing Private Limited* | BOM | 6,345 |
| Customer | 408 | Supplier | 1,284 |
| Sales Order | 645 | Sales Invoice | 1,928 |
| Purchase Order | 1,571 | Purchase Invoice | 1,817 |
| Purchase Receipt | 1,812 | Delivery Note | 489 |
| Stock Entry | 642 | Work Order | 445 |
| Stock Ledger Entry | 21,949 | GL Entry | 39,155 |
| File | 8,232 | Data Import | 169 (Data Import is actively used) |
| Report | 299 | Role | 153 |
| Custom Field | 906 | Workflow | 13 (6 active) |

DocType metadata: 1,116 DocTypes total; 464 child tables; 77 singles; 21 virtual; **220 non-child, non-single, non-virtual DocTypes with `allow_import = 1`**, of which **74 are submittable**.

System Settings: language `en`, timezone `Asia/Kolkata`, country India, date format `dd-mm-yyyy`, number format `#,##,###.##`, currency INR, `allowed_file_extensions` empty (all allowed), `max_file_size` not set (Frappe default 10 MB, `frappe/utils/file_manager.py:211`), 2FA off.

**Findings that affect Chikku AI directly:**

1. **`User Permission` count = 0.** No user is restricted to a company, warehouse or customer. Any user with a role on Sales Invoice can read both companies' invoices. Chikku will inherit exactly this. "Cross-company protection" therefore needs User Permissions configured *before* rollout. This is an ERPNext configuration task, not a Chikku feature.
2. **Hindi (`hi`) is not an enabled Language** in Frappe (17 languages enabled, Hindi not among them). Chikku's language handling does not depend on Frappe's UI translation, but if Hindi UI labels are wanted, the Language record must be enabled.
3. Active workflows exist on Item Price, Purchase Receipt, Production Plan, BOM, Purchase Invoice and Purchase Order. Chikku drafts land in each workflow's initial state and must never call `apply_workflow`.

## 3. The `amba_garments` app

| Item | Value |
|---|---|
| Path | `apps/amba_garments` |
| Git | remote `Simpel-Techlabs-Private-Limited/Amba-Garments`; current branch `test` (HEAD `224e414`); also `live`, `Test`, `*_v13` branches |
| Uncommitted work | modified fixtures (client/server scripts, workflows, roles, property setters, print formats, reports), `utils.py`; untracked `material_barcode` DocType, `purchase_order_tracking_report`, `api/barcode.py`, `public/js/barcode_print.js`, `node_modules` |
| Packaging | legacy `setup.py` + `requirements.txt` (only a comment); no `pyproject.toml` |
| Version | `0.0.1` |
| Licence | MIT (`license.txt`, `hooks.py`) |
| Modules (`modules.txt`) | `Amba Garments`, `User Restriction` |
| Tests | 6 boilerplate `test_*.py` DocType files; no real test suite |
| Lint/CI | none (no pre-commit, ruff config or GitHub workflows) |
| ~272 files | 13 DocTypes, ~29 script reports, 1 Desk page (`manufacturing_superv`), 13 print formats |

> ⚠ **The `test` branch has uncommitted changes.** Commit or stash them before any Chikku implementation starts, so Chikku changes stay in reviewable commits of their own.

### 3.1 Package layout (actual)

```
amba_garments/                       ← Python package root
├── hooks.py, utils.py, modules.txt
├── api/                             ← whitelisted/service code (barcode.py, purchase_order/*)
├── amba_garments/                   ← Module "Amba Garments"
│   ├── doctype/  (washing_send, washing_receipt, direct_dc, dc_item, purchase_order_bom,
│   │             invoice_import_tool, sales_invoice_import_row, sales_order_reconciliation_tool,
│   │             supplier_sub_group, dc_return_history, material_barcode*, + controller-only
│   │             folders bom/, production_plan/, purchase_receipt/, work_order/)
│   ├── report/   (~29 reports)
│   ├── page/manufacturing_superv/
│   ├── print_format/
│   ├── custom/   (customize-form JSON for 11 standard DocTypes)
│   └── overrides/item.py            ← override_doctype_class Item → CustomItem
├── user_restriction/                ← Module "User Restriction" (Access Control DocType, workspace)
├── fixtures/                        ← 11 fixture JSONs
├── public/js/                       ← plain JS (no .bundle.js); restrict_backdate.js, mobile_restriction.js, purchase_order/*
├── patches/, templates/, www/, config/
```

Convention observed: one Python package per Frappe module (`amba_garments/amba_garments`, `amba_garments/user_restriction`), shared services in `amba_garments/api/<area>/`. Chikku AI should follow the same pattern as a **third module**, `Chikku AI` → package `amba_garments/chikku_ai/` (Deliverable 6).

### 3.2 hooks.py (effective content)

| Hook | Value | Chikku impact |
|---|---|---|
| `fixtures` | `Workflow`, `Workflow State`, `Role` **unfiltered**; Custom Field, Property Setter, Notification, Print Format, Report, Client/Server Script, Web Page filtered by `module = "Amba Garments"` | Any **Role** Chikku creates (e.g. `Chikku AI User`) will be swept into `fixtures/role.json` on the next `export-fixtures`. Acceptable, but decide this deliberately. Chikku's own custom fields should use module `Chikku AI` so they are *not* exported via the Amba filters unless intended. |
| `doctype_js` | Purchase Order → 2 scripts | none |
| `override_doctype_class` | `Item` → `CustomItem` (`after_insert` disables auto Item Price) | Item drafts created by Chikku go through this override: expected. |
| `jinja.methods` | `get_purchase_order_qr_code` | none |
| `before_login` | user_restriction `before_login` (desktop/mobile restriction by Access Control) | Chikku is only reachable after login, so it inherits this. A future mobile/voice surface must respect Access Control. |
| `web_include_js` | `mobile_restriction.js` | — |
| `app_include_js` | `/assets/amba_garments/js/restrict_backdate.js` (plain asset path, not a bundle) | Chikku adds a second entry (`chikku_ai.bundle.js`). |
| `setup` | `overrides.item.setup` | — |
| `doc_events` | BOM `validate` | — |
| `scheduler_events` | daily cron PO→BOM job | — |

`api/barcode.py` defines `on_purchase_receipt_submit`/`cancel` but no `doc_events` registration for them exists in `hooks.py` yet. This is unfinished, uncommitted work, unrelated to Chikku.

### 3.3 Existing business rules a Chikku draft would meet

| Rule | Where | Enforced server-side? | Consequence for Chikku |
|---|---|---|---|
| **Backdate restriction**: `posting_date`/`transaction_date` must be ≥ today − 3 days unless role `Backdate Approval` (Sales Invoice form only) | `public/js/restrict_backdate.js` | **No: client-side only** | A server-side draft (Chikku, API, import) bypasses it. Chikku must either replicate the rule in its draft validator, or Amba must add a server-side validation (recommended; open question Q1). |
| PO must have PO Processing Owner / approval employee | Server Script "Purchase Order" Before Insert (enabled) | Yes | Chikku PO draft tool must collect `custom_po_processing_owner`, `custom_same_person`, `custom_approval_`. |
| Sales Invoice naming series by `external_ref_c` + company | Server Script "Sales Invoice Naming Series" (enabled) | Yes | SI drafts must set `external_ref_c` correctly or get the wrong series. |
| Material Request qty cross-check vs Production Plan | Server Script (disabled) | — | — |
| Delete yesterday's **draft** Delivery Notes | Server Script "Delete records in Delivery note" (**disabled**, but present) | — | If re-enabled, Chikku-prepared draft DNs would be silently deleted the next day. Flag in admin docs. |
| 17 Client Scripts on transactional forms | fixtures | Client-side | Logic in client scripts does **not** run for server-created drafts. Each must be reviewed for logic Chikku needs to mirror (open question Q2). |
| 906 Custom Fields, 322 Property Setters | fixtures | Meta | Chikku must read meta dynamically (`frappe.get_meta`) and never hardcode field lists. |
| Custom Sales Invoice importer | `invoice_import_tool` DocType | — | Overlaps with conversational import; Chikku should *call* it as a specialised path for Sales Invoices rather than replace it (open question Q3). |

## 4. Frappe v16 capabilities verified in source

| Capability | Location (frappe 16.52.0) | Conclusion |
|---|---|---|
| Desk route | `frappe/hooks.py:12,65,69` | Desk lives at **`/desk/...`**; `/app/*` redirects to `/desk/*`. The context extractor must parse `/desk/`. |
| Global JS | `frappe/hooks.py:24` `app_include_js` (+ esbuild `*.bundle.js` resolution) | Standard way to inject a global floating launcher. `saarthi_home` already does this with `saarthi_home.bundle.js`. |
| Frontend libs shipped | `frappe/package.json` | Vue 3, frappe-charts, showdown (`frappe.markdown`), socket.io-client, sortablejs, highlight.js. **No new npm dependency is needed for the widget.** |
| Router events | `frappe/public/js/frappe/router.js:73,200,736` | `frappe.router.on("change", …)` gives route changes. `form.js:611,670` triggers `form-load`/`form-refresh` on `$(document)`. |
| Permission-aware list | `frappe/__init__.py:1363` `get_list` → `frappe.model.qb_query.DatabaseQuery` (perm-checked unless `ignore_permissions`) | Use `frappe.get_list` for all Chikku reads; `get_all` = `ignore_permissions=True`. |
| Doc read with field-level perms | `frappe/client.py:95-115` (`check_permission` + `apply_fieldlevel_read_permissions`) | Chikku "get document" must call both. |
| Linked documents | `frappe/desk/form/linked_with.py:523` | Checks only DocType-level permission and counts with `get_all` (leaks counts). **Do not reuse directly**; build Chikku's own linked-doc tool on `get_list`. |
| Stock balance | `erpnext/stock/utils.py:97` `get_stock_balance` (checks Item read + User Permissions on Item/Warehouse) | Reusable inside a read tool. |
| Party dashboard | `erpnext/accounts/party.py:904` `get_dashboard_info` | Reusable for customer/supplier outstanding. |
| Mapped drafts | `sales_order.py:1213/1398/1718` `make_delivery_note`, `make_sales_invoice`, `make_purchase_order` | Return **unsaved** mapped docs, so drafts can reuse ERPNext's own mapping logic. |
| Workflow | `frappe/model/workflow.py:120` `apply_workflow` | Chikku must never call it. |
| Report execution | `frappe/desk/query_report.py:44,251` (`is_permitted()`, prepared reports) | Reports can be run as the user via `query_report.run`. Prepared reports are async. |
| Secrets | `frappe/utils/password.py:23,51` `get_decrypted_password`/`set_encrypted_password` (`__Auth` table, site `encryption_key`) | Store provider API keys in **Password** fields. |
| Realtime | `frappe/realtime.py:23` `publish_realtime(event, message, user=…, after_commit=…)` | Stream tokens/progress to a single user room. |
| Rate limiting | `frappe/rate_limiter.py:104` `@rate_limit(key, limit, seconds)` | Use on chat/upload endpoints. |
| HTML sanitising | `frappe/utils/html_utils.py:146` `sanitize_html`; `nh3` 0.3.6 installed | Server-side sanitising of rich output. |
| File privacy | `frappe/core/doctype/file/file.py` `is_private`, `validate_file_extension` | All Chikku uploads/outputs must be `is_private=1` and attached to a Chikku record. |
| **Data Import** | `frappe/core/doctype/data_import/*` | See §5. |
| **MCP server** | `frappe/api/v2.py:276-301` | **Not present in the installed build** (no `/api/v2/mcp` route, no MCP setting in System Settings). Frappe docs (updated 2026-10-27) describe a built-in MCP server [D]; it will likely arrive with a future upgrade. See Deliverable 4. |
| AI code in core | grep over frappe, erpnext, hrms, india_compliance, lending, saarthi_home | **None.** |

## 5. Native Data Import (v16.52.0), the key reuse candidate

| Aspect | Finding (file:line) |
|---|---|
| Controller | `data_import.py:53 class DataImport` |
| Fields | `reference_doctype`, `import_type` (Insert / Update / Insert or Update), `import_file` (Attach), `google_sheets_url`, **`template_options` (JSON column→field map)**, `value_mappings` (table), `skipped_rows` (table), `submit_after_import`, `mute_emails`, `status`, `payload_count`, delimiter options |
| Eligibility | `validate_doctype` (`:152`): blocks `BLOCKED_DOCTYPES = CORE_DOCTYPES − {User, Role, Print Format}` (`:23`), requires `meta.allow_import`, requires `frappe.has_permission(doctype, "import")` |
| DocType permission | Data Import itself: **System Manager only** (`data_import.json`) |
| Execution | `start_import` (`:196`) → RQ `default` queue, `timeout=10000`, `job_id=data_import||<name>`. **`run_now = frappe.in_test or developer_mode`**: on this dev-mode site imports run **synchronously inside the web request**. |
| Commit model | `importer.py:264-354`: batches of `data_import_batch_size` (default 1000); **commits after every successful record, rolls back only the failing record** → partial success is the norm; there is no all-or-nothing mode. |
| **Submission** | `importer.py:574`: `if meta.is_submittable and self.data_import.submit_after_import: new_doc.submit()` → **Chikku must force `submit_after_import = 0`** and guard against later edits (Deliverable 10). |
| Logs/progress | `Data Import Log` rows per record; `publish_realtime("data_import_refresh")`, progress events (`_publish_progress_event`) |
| Retry | `export_errored_rows` / `download_errored_template` |
| Stop | `stop_data_import` (`:252`) sends RQ stop command |
| Parent-child | Single-sheet template where child columns are `Table Field (Child Label)`, continuation rows with blank parent columns (`Header.get_column_indexes`, `Row.parse_doc`) |
| **Extension point** | `import_provider.py`: **`data_import_providers` hook**: an app class can take over field list, validation and `import_row` for a DocType. Lets Chikku plug in validation without core edits. |

## 6. Neighbouring apps and conflicts

- **saarthi_home** (same vendor, MIT, installed): `app_include_js = ["saarthi_home.bundle.js"]`, `extend_bootinfo`, caches keyed on User Permission changes. It shows the bundle conventions to copy. Coordinate z-index and placement so the Chikku launcher doesn't overlap Saarthi Home widgets.
- **jarvis** (on disk, *not installed*): a full AI assistant with floating widget, ~117 tools, ~25 scheduler jobs every 2–15 min, an external control plane (`fleet.klerk.in`, `wss://<slug>.jarvis.aerele.in`) and **AGPL-3.0** licence. Installing both would give two competing assistants, double scheduler load on a 1-vCPU box, and Jarvis's own data-egress model. See Deliverable 4.

## 7. Installed Python libraries relevant to Chikku (no installation needed)

| Library | Version | Licence / owner | Use |
|---|---|---|---|
| openpyxl | 3.1.5 | MIT, frappe dep | read/modify XLSX |
| XlsxWriter | 3.2.9 | BSD-2, frappe dep | generate XLSX (charts, CF, validation) |
| xlrd | 2.0.2 | BSD | legacy `.xls` read (2.x is xls-only) |
| pdfplumber / pdfminer.six | 0.11.10 / 20260107 | MIT | PDF text and tables |
| pypdf | 6.13.3 | BSD | PDF metadata/page ops |
| pypdfium2 | 5.12.1 | Apache/BSD | rasterise PDF pages for vision models |
| Pillow | 12.2.0 | HPND | image normalisation |
| lxml | 6.1.1 | BSD | DOCX XML parsing (python-docx is not installed) |
| nh3, bleach | 0.3.6, 6.3.0 | MIT/Apache | sanitising |
| pydantic | 2.12.5 | MIT | tool input/output schemas |
| requests | 2.33.1 | Apache | provider HTTP |
| filetype, chardet | | MIT/LGPL | content sniffing |
| rq / redis | 2.6.1 / 7.1.1 | | jobs |

**Missing** (would require approval to add): `defusedxml` (recommended, tiny), `numpy` (optional), `httpx`, provider SDKs (`openai`, `anthropic`, `google-genai`), `python-docx`, `python-magic`, `tiktoken`.

## 8. Access limitations of this research

- No access to Frappe Cloud/GitHub private repos beyond the local clone; the `live` branch of amba_garments was not diffed against `test`.
- The production server (if different from this one) was **not inspected**. Every figure above is from this `ambatest.local` bench. Re-run the probe on production before final sizing.
- MCP behaviour is from Frappe docs [D], not from code, because the installed build lacks it.
- Provider prices, latencies and language quality come from vendor pages [D] and must be measured in the bake-off (Deliverable 5).
