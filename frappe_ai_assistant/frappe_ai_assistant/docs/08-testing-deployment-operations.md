# Deliverables 13 and 14 — Testing Strategy, Deployment & Operations

---

# Part A — Testing Strategy (Deliverable 13)

Test runner: Frappe's `bench --site <test-site> run-tests --app amba_garments --module frappe_ai_assistant.frappe_ai_assistant.tests...` (FrappeTestCase). All provider calls are mocked through a `FakeProvider` adapter that replays scripted tool calls, so unit and integration suites need no network or API spend.

**Test site:** a dedicated `chikku-test.local` restored from a sanitised backup of `ambatest.local`. Never run tests on the production site.

## A1. Unit tests

| Area | Key cases |
|---|---|
| Provider adapters | request building per provider; SSE parsing incl. split chunks; tool-call assembly; usage extraction; error mapping (401/429/500/timeout); header redaction in exceptions |
| Router | slot resolution; fallback on circuit open; budget block |
| Tool registry/guard | unknown tool; extra args rejected; denylisted DocType; capability off; output caps; `NOT_FOUND_OR_NO_ACCESS` uniformity |
| Filters validator | invalid field, invalid operator, SQL fragments in values, order_by injection |
| Context resolver | `/desk/sales-order/X` parsing; unpermitted doc dropped; selected names re-checked |
| Language | Devanagari, Hinglish, English, mixed; identifier preservation post-check |
| Greeting | first name from DB; fallback; no repeat; template injection (`{__class__}`) inert |
| Tokens | expiry, replay, wrong user, param tamper, concurrent confirm (two threads → exactly one Done) |
| Excel reader | header detection (title rows, merged), types, zip-bomb fixture rejected, xlsm rejected, external links flagged, `data_only` values |
| Excel writer | formula-injection strings stored as text; INR formats; charts present (inspect zip parts) |
| Import mapper | exact/synonym/fuzzy/value-based; mandatory coverage; LLM output outside candidate list rejected |
| Transformer | flat → native parent/child; two-sheet join; orphans |
| Doc-intel validators | GSTIN checksum; arithmetic tolerance; CGST+SGST vs IGST |
| Lint test | AST scan: no `frappe.get_all`, `frappe.db.sql`, `ignore_permissions=True` in `tools/` and `services/erp/` |

## A2. Integration tests (Frappe test site)

- Read tools against fixtures created per test (two companies, users with/without User Permissions, a user with permlevel restrictions).
- Draft tools: insert as user → docstatus 0 → workflow initial state; Amba PO Server Script requirement surfaces as validation message.
- Native Data Import end-to-end: session → native file → preview → start (in RQ, `now=True` in test) → logs; **assert `submit_after_import` guard throws** when toggled; assert all imported docs docstatus 0.
- Report tool: run *Accounts Receivable* as limited user.
- Realtime: `publish_realtime` called with `user=` only (mock).
- Files: upload → private File → download by owner OK, by other user 403.
- Desk UI (Playwright, R1 end): launcher present on `/desk`, absent on print view; open/close; streaming render; action preview confirm; keyboard navigation; no console errors; no interference with Saarthi Home widgets.
- Provider contract tests (manual/nightly, real keys, tiny budget): one turn per adapter.

## A3. Security tests

| Test | Pass criterion |
|---|---|
| Permission bypass matrix: 12 DocTypes × 4 users × {search, get, linked, aggregate, report} | only permitted data returned; counts don't leak |
| Cross-company (after User Permissions set) | zero rows of other company in any tool/metric |
| Cross-user conversations/messages/files/action requests | 403/empty for all |
| Prompt-injection corpus (≥ 100 payloads in PDFs, XLSX cells, item descriptions, customer names) | no unconfirmed write; no denylisted tool attempted successfully; flags raised |
| Replay & tamper of tokens | rejected + Audit Log entry |
| Malicious files (zip bomb, XXE, macro, polyglot, huge image) | rejected or safely bounded; worker survives |
| Secret exposure | grep of all API responses, bootinfo, Error Log, realtime payloads for key patterns → none |
| XSS in rendered output | sanitiser strips scripts/handlers |
| Rate limits & budgets | enforced; 429 friendly message |
| Job identity | every job asserts session user == owner |

## A4. Performance tests

- 200k-cell XLSX profile < 20 s, worker RSS < 400 MB.
- Native import of 5,000 Items via Chikku: completes; progress updates ≥ every 2 s; web workers stay responsive (p95 Desk page < 2 s during import).
- Concurrency: 2 concurrent turns + 1 import + normal Desk usage on current host → measure; repeat on target 4 vCPU/8 GB.
- Long conversations (300 messages): history trimming keeps prompt < limit; panel virtualised scroll smooth.
- Slow provider (injected 60 s latency): turn times out gracefully at limit; queue doesn't starve.

## A5. User acceptance scenarios (measurable)

| Persona | Scenario | Acceptance |
|---|---|---|
| Sales executive | "SO-… kab deliver hoga?" on an SO | correct pending qty per item vs DB; answer in Hinglish; ≤ 20 s |
| Sales executive | draft quotation for customer X, 3 items | draft created only after confirm; values match preview |
| Purchase team | "Shree Textiles ke pending PO items" | matches *Purchase Order Tracking Report* |
| Purchase team | upload 3 supplier invoices → draft PIs | ≥ 90 % fields correct on test set; all low-confidence fields flagged; 0 silent errors on totals |
| Warehouse | "stock of item ABC in Finished Goods" | equals Stock Balance report |
| Accounts | "overdue invoices > 60 days for Amba Garments Limited" | equals AR report filters |
| Production | "work orders delayed this week" | matches *Work Order Production Lifecycle & Ageing* |
| Manager | "compare Oct vs Sep sales and explain" | totals reconcile to Sales Register; XLSX download opens without repair prompt |
| Administrator | change provider from A to B | no code change; next turn uses B; usage logged under B |
| Administrator | import 1,000 customers with mapping correction | preview errors fixed; import Success/Partial with errored rows XLSX |

Language UAT: 30 prompts per language per persona; ≥ 90 % rated "natural" by Hindi-speaking staff for Hinglish.

---

# Part B — Deployment & Operations Strategy (Deliverable 14)

## B1. Pre-requisites (before any implementation merges to `live`)

1. **Commit/stash the current uncommitted work** on `amba_garments` `test` branch (Deliverable 1 §3).
2. Create branch `feature/chikku-ai` from `test`.
3. Infrastructure for production (decision Q7): **≥ 4 vCPU, 8 GB RAM**, plus one dedicated supervisor program `frappe-bench-frappe-chikku-worker` running `bench worker --queue chikku`. On the current 1 vCPU / 1.9 GB box, run only pilot usage with `max_concurrent_turns=1`.
4. Configure **User Permissions on Company** for users who should be restricted (Q5).
5. Provider contracts: DPA, zero-retention/no-training confirmation, billing limits at provider side.
6. HTTPS on the site (required for microphone in R2).
7. Add `defusedxml` to `amba_garments` dependencies (approval needed). Convert to `pyproject.toml` optional; `setup.py` works with current bench.

## B2. Installation inside `amba_garments`

- Code under `amba_garments/chikku_ai/` + `public/js/chikku_ai/`; `modules.txt` gains `Chikku AI`.
- `hooks.py` additive changes only (Deliverable 6 §A1).
- Migration: `bench --site <site> migrate` creates DocTypes; `after_migrate` hook seeds roles and default settings idempotently (disabled by default: `enabled=0`).
- Assets: `bench build --app amba_garments`.
- Restart: `bench restart` (supervisor) in a maintenance window.
- Feature flag: Chikku stays **disabled** until an admin enables it and assigns `Chikku AI User` to pilot users.

## B3. Release sequencing

| Release | Scope | Gate |
|---|---|---|
| R0 | provider bake-off, User Permissions, infra | ADR signed |
| R1 | widget, settings, conversations, read tools, context, analytics metrics, Excel analyse/generate, file upload, activity centre (basic), audit/usage | security test suite green; 2-week pilot with 3 users |
| R1.5 | draft tools (SO, Quotation, PO, MR, mapped drafts) | 2 weeks of R1 audit review, 0 permission incidents |
| R2 | conversational import, document intelligence, push-to-talk, Excel modify, full activity centre | |
| R3 | knowledge assistant, workflow proposals, anomaly detection, guidance | MariaDB/vector decision |
| R4 | continuous voice, proactive notifications | relay service decision |

## B4. Configuration management

- Chikku Settings/Providers are **site data**, not fixtures (contain secrets). Document a manual setup checklist; export non-secret settings via a JSON admin export if needed.
- Roles are created by code and will also appear in `fixtures/role.json` on export (acceptable).
- Never put Chikku Custom Fields in module `Amba Garments` (keeps fixture filters clean).

## B5. Backup and recovery

- Standard `bench backup --with-files` covers all Chikku DocTypes and private files.
- `__Auth` passwords are encrypted with the site's `encryption_key`: back up `site_config.json` securely together with the DB, or the provider keys become undecryptable after restore.
- Recovery test each quarter: restore to a test site, verify Chikku opens with keys intact.

## B6. Monitoring

| Signal | Source | Alert |
|---|---|---|
| Turn error rate | Message.status | > 5 % / 15 min |
| Provider failover | Audit Log `provider_failover` | any |
| Queue depth `chikku` | RQ | > 10 jobs |
| Turn p90 latency | Usage Log | > 30 s |
| Cost | Usage Log | 80 % of monthly budget |
| Security events | Audit Log severity ≥ high | any |
| Worker memory | supervisor/OS | RSS > 600 MB |

Delivered via Frappe Notifications to Chikku AI Managers, with optional email.

## B7. Rollback

1. Instant: uncheck `enabled` in Chikku Settings (kill switch) → launcher hidden, APIs refuse.
2. Code rollback: revert the `amba_garments` commit range for Chikku and rebuild/restart. DocTypes remain (harmless) unless a cleanup patch is run.
3. Full removal (if ever): patch that deletes Chikku DocTypes/records after an export; Data Imports and drafts created by Chikku are normal ERPNext records and remain.

## B8. Operations runbook items

Rotate provider keys (Password field edit; no restart); purge retention (daily job); investigate a user complaint (Message → Tool Calls → Audit Log); disable a capability for a role; handle provider outage (switch slot binding); verify native MCP setting after every Frappe upgrade.
