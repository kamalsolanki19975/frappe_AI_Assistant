# Chikku AI — Research Package & Final Architecture Recommendation (Deliverable 15)

Prepared 2026-11-03 from a read-only inspection of `/home/frappe/frappe-bench` (site `ambatest.local`) plus source and documentation research. **No code, hooks, DocTypes, packages, services or data were changed.**

## Index

| File | Deliverables |
|---|---|
| `00-README-final-recommendation.md` | 15 — final recommendation, decisions, risks, open questions |
| `01-environment-discovery.md` | 1 — environment and `amba_garments` discovery |
| `02-product-requirements-multimodal-ux.md` | 2 PRD · 9 multimodal spec · 12 UI/UX spec |
| `03-feasibility-solutions-providers.md` | 3 feasibility · 4 existing-solution comparison · 5 provider architecture |
| `04-architecture-data-model-tools.md` | 6 architecture · 7 DocType design · 8 API & tool registry |
| `05-excel-and-import.md` | 10 Excel and import |
| `06-security-threat-model.md` | 11 threat model |
| `07-capability-designs.md` | analytics, document intelligence, knowledge, voice, workflow, proactive, guidance |
| `08-testing-deployment-operations.md` | 13 testing · 14 deployment & operations |

## 1. Top findings (read these first)

1. **Hardware is the binding constraint.** This server has **1 vCPU and 1.9 GB RAM, with 1.4 GB swap already in use**. All model, speech, OCR and embedding work must use external APIs, and production needs ≥ 4 vCPU / 8 GB plus a dedicated AI worker.
2. **There are no User Permissions on the site (0 records) across 2 companies.** Chikku will enforce ERPNext permissions exactly. That means it cannot provide cross-company isolation until User Permissions are configured.
3. **Some Amba business rules are client-side only** (the 3-day backdate rule in `restrict_backdate.js`, plus 17 client scripts). Drafts created server-side bypass them unless the rules are mirrored or moved server-side.
4. **Native Data Import is reusable and extensible** (`template_options` mapping, `data_import_providers` hook, realtime progress, errored-row export). However, it **submits documents when `submit_after_import` is set** (`importer.py:574`), commits row by row, is System-Manager-only by default, and runs synchronously in developer mode.
5. **Frappe's built-in MCP server is not in the installed 16.52.0 build.** The docs describe it with delete and submit/cancel reach, enabled by default on new sites. It must not be wired into Chikku, and its setting must be reviewed after upgrades.
6. **`jarvis` (AGPL-3.0, hosted agent runtime) is on disk but not installed.** It's useful as a design reference, but don't install it alongside Chikku and don't copy its code into the MIT-licensed `amba_garments`.
7. **Nearly everything needed is already installed**: openpyxl, XlsxWriter, pdfplumber, pypdf, pypdfium2, Pillow, lxml, nh3, pydantic, requests; and on the frontend Vue 3, frappe-charts, showdown and socket.io. The only recommended addition is `defusedxml`.
8. The `amba_garments` `test` branch has **uncommitted changes** that should be committed before Chikku work starts.

## 2. Recommended architecture (one paragraph)

Chikku AI is a new Frappe module, **`Chikku AI`**, inside `amba_garments` (`amba_garments/chikku_ai/`), with a lazy-loaded Vue 3 floating panel injected via `app_include_js`. Thin whitelisted APIs store messages and enqueue **turns in RQ** (never an LLM call inside a web request). An orchestrator calls a **provider-agnostic adapter layer** (thin in-house adapters over `requests`; model "slots" for reasoning/fast/vision/STT/TTS/embedding). The model can only call a **fixed, schema-validated tool registry**. Tools execute **as the signed-in user** through permission-aware Frappe APIs, and a CI lint bans permission-bypassing calls. Every write becomes a **draft** behind a single-use, user- and parameter-bound confirmation token that the model never sees. Spreadsheets are read with openpyxl and generated with XlsxWriter. Imports are transformed into the **native Data Import** format and executed by Frappe's own importer, with a three-layer guard against submission. Documents are extracted by a vision model and checked by deterministic validators. Knowledge search uses MariaDB FULLTEXT plus embedding re-ranking until scale justifies MariaDB 11.8's native vectors. Streaming and progress go over Frappe realtime to the user's room only. Everything is audited and budgeted.

## 3. Decisions, alternatives and reasons

| # | Decision | Alternatives considered | Reason |
|---|---|---|---|
| D1 | New module `Chikku AI` → package `amba_garments/chikku_ai/` | put code in `amba_garments/amba_garments/` or `api/` | matches the app's one-package-per-module convention (`user_restriction`); clean fixtures and permissions separation; required "no separate app" respected |
| D2 | DocType prefix `Chikku AI …` | generic `AI …` | avoids clashing with future Frappe AI DocTypes/settings |
| D3 | LLM calls only in RQ jobs; realtime streaming | SSE from gunicorn | 3 sync gunicorn workers with 120 s timeout on 1 vCPU would be starved |
| D4 | Thin in-house provider adapters on `requests` | vendor SDKs; LiteLLM in-process; gateway service | no new deps or pin conflicts; LiteLLM's March 2026 supply-chain compromise; SDKs remain an acceptable later swap behind the same interface |
| D5 | Fixed tool registry, user-context execution, no SQL/code | Native MCP; FAC; generic `call_method` tool | brief's security rules; MCP and FAC expose delete/submit/code |
| D6 | Draft-first with server tokens hidden from the model | model-confirmed writes; "low-risk auto-apply" (Jarvis style) | strongest defence against prompt injection; matches approved policy |
| D7 | Reuse native Data Import as execution engine | custom importer | native already handles validation, logs, progress, retry; brief prefers reuse |
| D8 | Data Import access Option B (Custom DocPerm for `Chikku AI Importer`, if_owner) | A: System Manager only; C: ignore_permissions | least privilege without core changes |
| D9 | XlsxWriter for output, openpyxl for input; rebuild-on-modify | openpyxl for everything | openpyxl loses charts/images on save; XlsxWriter is richer and faster for output |
| D10 | Vision LLM + validators for document intelligence | Tesseract; self-hosted PaddleOCR; Document AI | Hindi quality, hardware, simplicity; Document AI kept as POC option |
| D11 | Knowledge: MariaDB FULLTEXT + embedding re-rank | Qdrant/pgvector; Redis Stack | no new service on a starved host; upgrade path to MariaDB 11.8 VECTOR |
| D12 | STT/TTS via Sarvam (primary candidate) | OpenAI/Google; browser Web Speech | Indic/code-mixed focus, India hosting; browser API inconsistent and has privacy issues |
| D13 | Message as a separate normal DocType | child table of Conversation | avoids rewriting whole conversations; scales to long histories |
| D14 | Merge Execution Log → Tool Call; Feedback → Message fields; Import Log → native Data Import Log | separate DocTypes | fewer DocTypes, same information |
| D15 | Provider selection by measured bake-off (450 prompts, EN/HI/Hinglish) | pick the most popular | brief requirement; Hinglish quality differs from Hindi quality |

## 4. Dependencies

- **Existing:** Frappe 16.52.0, ERPNext 16.51.1, RQ/Redis, socket.io, MariaDB 10.11, listed Python/JS libs.
- **New Python (approval):** `defusedxml` (recommended). Optional later: `numpy` (vector math speed), provider SDKs.
- **External services:** one primary LLM provider (+ fallback), Sarvam (voice, R2), an embedding provider (R3).
- **Infrastructure:** larger host + dedicated `chikku` worker (production); HTTPS; optional MariaDB 11.8 upgrade (R3+); optional voice relay service (R4).

## 5. Key risks

| Risk | Impact | Mitigation |
|---|---|---|
| Under-sized server | timeouts, Desk slowness | upgrade before rollout; concurrency caps; pilot only on current box |
| Company isolation not configured | data exposure between companies | configure User Permissions before R1 go-live |
| Client-side-only rules | invalid drafts | mirror rules in Chikku validators; move to server-side (Q1) |
| LLM factual errors in narrative | wrong decisions | deterministic numbers, doc cards, "AI-generated" labelling, feedback loop |
| Provider data processing | confidentiality/compliance | DPA, zero retention, toggle for document content, masking |
| Hinglish quality | adoption | bake-off; language-specific evaluation |
| Scope size | delay | strict release gates R1→R4 |
| Future Frappe upgrade adds MCP enabled | bypass of draft-first via API tokens | upgrade checklist item |

## 6. Open questions for the business (need answers before implementation)

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | Should the 3-day backdate rule be enforced server-side for **all** channels (a small change in `amba_garments`), or only inside Chikku? | Chikku-only enforcement |
| Q2 | Which of the 17 Client Scripts contain business rules that drafts must respect? (needs owner walkthrough) | review all before R1.5 |
| Q3 | Should Sales Invoice imports always go through the existing *Invoice Import Tool*? | yes |
| Q4 | Data Import access model: A, B or C (Deliverable 10 §B10)? | B |
| Q5 | Which users must be restricted to which company/warehouse? | none → isolation not guaranteed |
| Q6 | May Chikku AI Managers read other users' conversations (privacy policy)? | **no**: metadata/usage only |
| Q7 | Production host size and whether production is this server or another one | must be confirmed; production not inspected |
| Q8 | Preferred/acceptable AI providers and data-residency requirements (India-only?) | bake-off among Anthropic, OpenAI, Google; Sarvam for voice |
| Q9 | Monthly AI budget ceiling | set before R1 |
| Q10 | Retention periods for messages, files, audio, audit | 180 / 30 / 0 / 730 days |
| Q11 | Is the disabled "delete yesterday's draft Delivery Notes" server script ever re-enabled? | treat as risk; warn in admin docs |
| Q12 | Should Hindi UI labels be enabled (Frappe `hi` language)? | no; Chikku handles its own strings |

## 7. Technical limitations

- No all-or-nothing imports (native per-record commits).
- Formulas in uploaded workbooks are not calculated; cached values only.
- Modified workbooks with charts/pivots are rebuilt, so original chart objects may not be preserved exactly.
- Continuous voice needs a component outside gunicorn.
- Semantic search scale is limited on MariaDB 10.11.
- ERPNext controller validations aren't covered by the native import preview (deep dry-run is a POC).

## 8. Future extension points

- Admin-defined read tools (declarative: DocType + allowed fields + filters), as in Raven's function UI.
- Exposing Chikku's **read-only** registry as an MCP server for desktop AI clients.
- `data_import_providers` hook for DocType-specific import validation.
- Additional provider adapters (Azure OpenAI India regions, self-hosted vLLM when hardware allows).
- MariaDB 11.8 VECTOR migration for knowledge.
- Mobile/PWA surface reusing the same API.

## 9. Evidence references (main)

| Conclusion | Source |
|---|---|
| Submission on import | `apps/frappe/frappe/core/doctype/data_import/importer.py:574` |
| Per-record commit | same file `:347`, `:354` |
| Import provider hook | `.../data_import/import_provider.py:1-50` |
| Import eligibility + blocked DocTypes | `.../data_import/data_import.py:23,152-168` |
| Sync import in dev mode | `.../data_import/data_import.py:198` |
| Data Import perms | `.../data_import/data_import.json` (System Manager) |
| Desk route `/desk` | `apps/frappe/frappe/hooks.py:12,65,69` |
| get_list permission-aware | `apps/frappe/frappe/__init__.py:1363` → `model/qb_query.py:125` |
| Field-level read perms | `apps/frappe/frappe/client.py:114-115` |
| Linked docs perm gap | `apps/frappe/frappe/desk/form/linked_with.py:541,593` |
| Stock balance perms | `apps/erpnext/erpnext/stock/utils.py:97-108` |
| Password storage | `apps/frappe/frappe/utils/password.py:23,51` |
| Realtime | `apps/frappe/frappe/realtime.py:23` |
| Rate limiter | `apps/frappe/frappe/rate_limiter.py:104` |
| No MCP in build | `apps/frappe/frappe/api/v2.py:276-301` |
| Frappe MCP docs | https://docs.frappe.io/framework/guides-integration/mcp |
| Backdate rule client-only | `apps/amba_garments/amba_garments/public/js/restrict_backdate.js` |
| Jarvis licence/runtime | `apps/jarvis/jarvis/hooks.py`, `apps/jarvis/jarvis/agent_ws.py` |
| Frappe Assistant Core | https://github.com/buildswithpaul/Frappe_Assistant_Core |
| ERPNext Copilot | https://github.com/ameenmari/erpnext_copilot |
| Raven | https://github.com/The-Commit-Company/raven |
| LiteLLM compromise | https://www.arthur.ai/column/litellm-supply-chain-attack-pypi-compromise-2026 |
| MariaDB vectors | https://mariadb.com/docs/server/reference/sql-structure/vectors/vector-overview |
| Sarvam STT/TTS | https://docs.sarvam.ai/api-reference-docs/building-for-india , https://sarvam.ai/apis/text-to-speech |

## 10. Next step

Review this package, answer Q1–Q12 and approve or amend D1–D15. Then a single implementation brief for **R0 + R1** can be written directly from Deliverables 6–8, 10, 11 and 13.
