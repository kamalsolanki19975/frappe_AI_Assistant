# Deliverables 6, 7, 8 — Technical Architecture, DocType Design, API & Tool Registry

---

# Part A — Technical Architecture (Deliverable 6)

## A1. Placement inside `amba_garments` (follows observed conventions)

New Frappe module **`Chikku AI`** (added as a third line in `modules.txt`), package `amba_garments/chikku_ai/`:

```
apps/amba_garments/amba_garments/
├── chikku_ai/                         ← Module "Chikku AI" (DocTypes, pages, workspace)
│   ├── __init__.py
│   ├── doctype/                       ← all Chikku DocTypes (Part B)
│   ├── page/chikku_activity/          ← Activity Centre page
│   ├── workspace/chikku_ai/
│   ├── api/                           ← ONLY whitelisted entry points (thin)
│   │   ├── chat.py  files.py  actions.py  imports.py  voice.py  admin.py
│   ├── orchestrator/                  ← turn loop, prompt building, output blocks
│   │   ├── turn.py  prompts.py  blocks.py  language.py  greeting.py  context.py
│   ├── providers/                     ← adapters (Deliverable 5)
│   ├── tools/                         ← registry + one module per tool group
│   │   ├── registry.py  guard.py  schemas.py
│   │   ├── read_docs.py  read_stock.py  read_party.py  analytics.py  reports.py
│   │   ├── drafts.py  excel.py  imports.py  documents.py  knowledge.py  workflow_proposals.py
│   ├── services/                      ← ERP business logic, no LLM imports
│   │   ├── erp/ (sales.py, purchase.py, stock.py, accounts.py, linked.py, metrics.py)
│   │   ├── excel/ (reader.py, writer.py, inspector.py, safety.py)
│   │   ├── importer/ (eligibility.py, mapper.py, transformer.py, native.py, provider_hook.py)
│   │   ├── docintel/ (extract.py, validators.py, gst.py, matcher.py)
│   │   ├── knowledge/ (chunker.py, indexer.py, retriever.py)
│   │   └── voice/ (stt.py, tts.py)
│   ├── security/                      ← permission helpers, action tokens, sanitisers, limits
│   │   ├── perms.py  tokens.py  untrusted.py  files.py  ratelimit.py  redaction.py
│   ├── audit/                         ← audit + usage writers (deferred insert)
│   ├── jobs/                          ← RQ job functions (run_turn, parse_file, index_doc, purge)
│   └── tests/
└── public/js/chikku_ai/
    ├── chikku_ai.bundle.js            ← tiny loader (launcher + lazy import)
    ├── chikku_ai.bundle.css
    └── panel/ (App.vue, components/*.vue, store.js, realtime.js, render.js)
```

**Rule:** `api/` → `orchestrator/` → `tools/` → `services/`. Services never import orchestrator or providers. Tools are the only bridge, which keeps ERP logic testable without an LLM and provider-agnostic.

**hooks.py additions (implementation phase, listed for approval only):**

```python
app_include_js = ["/assets/amba_garments/js/restrict_backdate.js", "chikku_ai.bundle.js"]
app_include_css = ["chikku_ai.bundle.css"]
extend_bootinfo = ["frappe_ai_assistant.frappe_ai_assistant.boot.extend_bootinfo"]
doc_events = { ..., "Data Import": {"validate": "frappe_ai_assistant.frappe_ai_assistant.services.importer.native.guard_data_import"} }
scheduler_events["cron"]["*/10 * * * *"] = ["frappe_ai_assistant.frappe_ai_assistant.jobs.maintenance.expire_action_requests"]
scheduler_events["daily"] = ["frappe_ai_assistant.frappe_ai_assistant.jobs.maintenance.purge_retention"]
# R2: data_import_providers = {} only if a DocType needs custom validation
```

## A2. System architecture

```
┌──────────────── Browser (Desk) ────────────────┐
│ chikku_ai.bundle.js  (Vue panel)               │
│   ├─ REST: /api/method/frappe_ai_assistant.frappe_ai_assistant.api.*   (CSRF, session cookie)
│   └─ socket.io: user room ◄── chikku_ai_stream / chikku_ai_job_progress
└──────────────────────┬─────────────────────────┘
                       │
┌──────────── nginx ───┴──── gunicorn (3 workers) ───────────────────────────┐
│ api/chat.send_message: validate, rate-limit, store Message, enqueue turn,  │
│ return {message_id} in < 300 ms (no LLM call in request)                    │
└──────────────────────┬─────────────────────────────────────────────────────┘
                       │ RQ (Redis queue)  queue = "chikku" (new) or "long"
┌──────────────────────┴─────────────────────────────────────────────────────┐
│ RQ worker  (runs as the requesting user via frappe.enqueue → set_user)      │
│  jobs.run_turn → Orchestrator                                               │
│    ├─ ContextResolver (revalidate)                                          │
│    ├─ PromptBuilder (system, settings, context card, untrusted wrappers)    │
│    ├─ ProviderRouter ──HTTPS──► LLM / STT / TTS / Embeddings APIs           │
│    ├─ ToolGuard ─► Tool ─► Services ─► frappe ORM (permission-aware)        │
│    ├─ ActionRequest (drafts) — no write without confirm endpoint            │
│    └─ publish_realtime(stream deltas, blocks, usage)                        │
└───────────────┬─────────────────────────────┬──────────────────────────────┘
          MariaDB 10.11                   Redis cache
  (Chikku DocTypes, Files metadata,   (locks, rate limits, circuit breakers,
   FULLTEXT chunks, audit, usage)      token single-use markers, stream buffer)
                  │
          sites/ambatest.local/private/files  (uploads, generated outputs)
```

## A3. Sequence — chat turn with read tools

```
User      Panel             api.chat           RQ worker / Orchestrator         Provider       Tools/Services
 │ ask ──► │ POST send_message ─►│ authz(role,cap), rate_limit, budget
 │         │                     │ insert Message(user), enqueue run_turn(msg) ─► 
 │         │ ◄── {message_id} ───│
 │         │                                     │ resolve context (has_permission)
 │         │                                     │ build prompt ─────────────────► chat(stream, tools)
 │         │ ◄── realtime deltas ────────────────│ ◄──── tool_call(search_documents, args)
 │         │                                     │ guard: schema, capability, perms ─────► get_list(as user)
 │         │                                     │ ◄──────────── result (capped, refs) ──┘
 │         │                                     │ tool_result ─────────────────► continue
 │         │ ◄── deltas + blocks ────────────────│ ◄──── final text
 │         │                                     │ save assistant Message, usage, audit
 │         │ ◄── done ───────────────────────────│
```

Loop limits per turn: ≤ 8 tool calls, ≤ 4 model round-trips, ≤ 120 s, ≤ 60k input tokens. Exceeding a limit ends the turn gracefully with a partial answer.

## A4. Sequence — draft write (draft-first, 10 steps)

```
1 model calls prepare_purchase_order(args)
2 guard: schema + capability "draft.purchase_order" + has_permission(PO,"create")
3 service builds an unsaved doc in memory (frappe.new_doc / ERPNext mapper) and runs Chikku
  pre-validation only (mandatory fields, links exist & permitted, company, backdate rule).
  ERPNext's own validate/server scripts run later, at insert time (step 8), so nothing is written now.
4 creates Chikku AI Action Request {status=Pending, action, params_json (canonical), params_hash,
  preview_json, user, conversation, expires_at=now+10min, token_hash=sha256(token)}
5 returns action_preview block (token sent ONLY to the browser via realtime, never to the model)
6 user clicks Create draft → POST api.actions.confirm(action_request, token)
7 server: same user? not expired? sha256(token)==token_hash? status Pending → atomically set Executing
  (SELECT … FOR UPDATE / conditional UPDATE … WHERE status='Pending')
8 re-run permission + business validation on current DB state; rebuild doc from params_json
  (not from preview), doc.insert() as user (docstatus 0) — ERPNext validate/server scripts run here
9 reload doc, verify docstatus==0 and key fields == params; store result_doctype/result_name; status Done
10 return doc_card; audit log; tell model "created draft X" for the narrative
```

The model **never sees the token**, so a prompt-injected model cannot confirm actions. Confirmation needs a real user click plus the CSRF-protected session.

## A5. Permission architecture

```
Layer 0  Login + Access Control (existing before_login)            [existing]
Layer 1  Role gate: "Chikku AI User" (System Manager implied)        [api decorator]
Layer 2  Capability gate: settings.role_capabilities                 [ToolGuard]
Layer 3  Tool allowlist: only registry tools, schema-validated       [ToolGuard]
Layer 4  Frappe permissions AS THE USER:                             [services]
         get_list / has_permission / check_permission / apply_fieldlevel_read_permissions
         → role perms, User Permissions, if_owner, sharing, permission_query_conditions,
           has_permission hooks, permlevels
Layer 5  Chikku business policy: no submit/cancel/delete/workflow; DocType denylist;
         company scoping; backdate rule; row caps                    [services + guard]
Layer 6  Confirmation token for every write                           [security/tokens]
Layer 7  Audit                                                        [audit]
```

Rules enforced in code review and by tests:
- `ignore_permissions=True`, `frappe.get_all`, `frappe.db.sql`, `frappe.db.get_value` on business DocTypes are **forbidden in `tools/` and `services/erp/`** (lint rule via a simple AST test in CI). Exceptions live only in `security/` and Chikku's own DocTypes, each with a comment.
- Background jobs: `frappe.enqueue` runs jobs as `frappe.session.user` of the enqueuer (verified pattern in Data Import: `start_import` enqueued from user request). Scheduled jobs that act for a user call `frappe.set_user(owner)` explicitly and re-check the role gate. Jobs never run as Administrator for user work.

## A6. File processing architecture

```
upload (api.files.upload)  → size/type check (extension + magic bytes via `filetype`)
  → save File(is_private=1, attached_to=Chikku AI Conversation)
  → Chikku AI Attachment row (status=Queued)
  → enqueue parse_file (queue short, timeout 120s)
       ├ xlsx/csv: safety.inspect_zip → openpyxl read_only, data_only → profile → store summary JSON
       ├ pdf: pdfplumber text/tables; low-text pages → pypdfium2 render → images
       ├ docx: zipfile+lxml (no entity resolution) → text
       └ image: Pillow verify/normalise
  → status=Ready, summary_json, page_count, warnings → realtime "attachment_ready"
```

Heavy parsing should run in a **subprocess with memory and time limits** (`resource.setrlimit` RLIMIT_AS ≈ 512 MB, 60 s CPU), so a hostile file cannot take down the RQ worker. Jarvis uses the same isolation idea (`_file_scan_process.py`).

## A7. Import architecture (summary; full design in Deliverable 10)

`Chikku AI Import Session` (mapping, transforms, status) → transformer writes a **native-format XLSX** → creates native `Data Import` (`submit_after_import=0`, `mute_emails=1` default, `import_type`) → preview via `get_preview_from_template` → user confirms → `form_start_import` → progress via native realtime events (relayed) → results from `Data Import Log` → errored rows export.

## A8. Background-job architecture

| Job | Queue | Timeout | Concurrency guard |
|---|---|---|---|
| `run_turn` | `chikku` (new dedicated worker) — fallback `long` | 180 s | per-user Redis lock: 1 active turn/user; site semaphore: N turns (N=2 now) |
| `parse_file` | `short` | 120 s | per-file job_id |
| `generate_workbook` | `long` | 600 s | per-session job_id |
| `transform_import` | `long` | 600 s | per-session |
| native Data Import | `default` (Frappe's choice) | 10000 s | native job_id |
| `index_knowledge_doc` (R3) | `long` | 600 s | per-doc |
| `stt_transcribe` (R2) | `short` | 60 s | — |
| maintenance (expire tokens, purge) | scheduler | — | — |

**Current-box caveat:** the only two workers serve `short,default` and `long,default,short`. A long Chikku job would block Frappe's own jobs (emails, Data Import, prepared reports). A dedicated `chikku` worker (supervisor program) is needed before production. That is an infrastructure change, listed in Deliverable 14.

Realtime: `frappe.publish_realtime("chikku_ai_stream", {...}, user=owner)`; deltas batched every ~100 ms to limit socket load.

---

# Part B — Database & DocType Design (Deliverable 7)

Naming: prefix **`Chikku AI`** (product name) rather than generic "AI …", to avoid collisions with future Frappe "AI" DocTypes (Frappe already has an *AI Agents* settings tab upstream [D]).

Consolidation vs. the 14 candidates in the brief:

| Candidate | Decision |
|---|---|
| AI Assistant Settings | **Chikku AI Settings** (Single) |
| AI Provider Configuration | **Chikku AI Provider** (normal, few rows) + child **Chikku AI Model** |
| AI Conversation | **Chikku AI Conversation** |
| AI Message | **Chikku AI Message** (normal, *not* child: avoids rewriting the whole conversation doc per message and allows large histories) |
| AI User Preferences | **Chikku AI User Preference** (normal, autoname = user) |
| AI Action Request | **Chikku AI Action Request** |
| AI Execution Log | merged into **Chikku AI Tool Call** (one row per tool call) |
| AI Audit Log | **Chikku AI Audit Log** (security events + writes; append-only) |
| AI Feedback | fields on Message (`feedback`, `feedback_note`): no separate DocType |
| AI Spreadsheet Job | **Chikku AI File Job** (covers excel generate/modify, doc extraction) |
| AI Import Session | **Chikku AI Import Session** |
| AI Column Mapping | child **Chikku AI Import Mapping** |
| AI Import Template | **Chikku AI Import Template** (R2, saved mappings) |
| AI Import Log | **not created**: use native `Data Import Log` + session summary |
| (new) usage | **Chikku AI Usage Log** (tokens/cost per model call) |
| (new) attachments | child **Chikku AI Message Attachment** |
| (new) capabilities | child **Chikku AI Role Capability** |
| (R3) knowledge | **Chikku AI Knowledge Collection**, **Chikku AI Knowledge Source**, **Chikku AI Knowledge Chunk** |

Total: 11 normal/single + 5 child (R1–R2), 3 more in R3.

## B1. Chikku AI Settings (Single)

| Field | Type | Req | Notes |
|---|---|---|---|
| enabled | Check | | kill switch |
| assistant_name | Data | ✓ | default "Chikku AI" |
| avatar | Attach Image | | public is fine (non-sensitive) |
| description | Small Text | | |
| welcome_message_en / _hi / _hinglish | Small Text | | templates with `{first_name}`, `{assistant_name}` only |
| greeting_enabled | Check | | |
| personality | Select | | Friendly-Professional / Concise / Detailed |
| system_instructions | Long Text | | admin additions appended to the fixed core prompt (cannot remove safety rules) |
| suggested_prompts | Table → Chikku AI Suggested Prompt | | prompt, language, doctype filter |
| default_language | Select | | Auto / English / Hindi / Hinglish |
| reasoning_model, fast_model, vision_model, embedding_model | Link → Chikku AI Model | | slot bindings |
| stt_model, tts_model | Link → Chikku AI Model | | |
| fallback_reasoning_model | Link → Chikku AI Model | | |
| temperature | Float | | default 0.2 |
| max_output_tokens | Int | | |
| max_tool_calls_per_turn | Int | | 8 |
| turn_timeout_seconds | Int | | 120 |
| max_concurrent_turns | Int | | 2 |
| user_daily_token_budget | Int | | |
| site_monthly_cost_budget | Currency | | hard stop |
| max_upload_mb, max_files_per_message | Int | | 15, 8 |
| retention_messages_days, retention_files_days, retention_audio_days, retention_audit_days | Int | | 180/30/0/730 |
| role_capabilities | Table → Chikku AI Role Capability | | |
| draft_doctypes_enabled | Small Text (JSON list) / Table MultiSelect | | allow-list for draft tools |
| send_doc_content_to_provider | Check | | privacy switch for document intelligence |

Permissions: read/write **System Manager, Chikku AI Manager**. No other role reads it; the panel gets only safe values via `extend_bootinfo`.

## B2. Chikku AI Provider / Chikku AI Model

**Chikku AI Provider** (naming: `field:provider_name`)

| Field | Type | Notes |
|---|---|---|
| provider_name | Data ✓ | unique |
| provider_type | Select ✓ | OpenAI-Compatible / Anthropic / Gemini / Sarvam |
| base_url | Data | validated https (http only for localhost in dev) |
| api_key | **Password** | never serialised to client (`Password` fields return `*****`) |
| organization / project_id / region | Data | |
| enabled | Check | |
| timeout_seconds, max_retries | Int | |
| data_residency_note | Small Text | documentation for admins |
| models | Table → **Chikku AI Model** (child) | model_id, label, slot types (MultiCheck), supports_tools/vision/json/stream, context_window, input_price_per_mtok, output_price_per_mtok, currency |

Permissions: System Manager only (Chikku AI Manager can read but **not** see the key: give `api_key` `permlevel 1`, granted only to System Manager).

> Settings link to models by a separate key. Since the model rows are children, use a `Chikku AI Model` **normal** DocType instead of a child if Link fields are needed (child rows cannot be Link targets). **Decision: make Chikku AI Model a normal DocType** with `provider` Link.

## B3. Chikku AI Conversation

| Field | Type | Req | Notes |
|---|---|---|---|
| title | Data | | auto-generated by `fast` slot |
| user | Link User | ✓ | = owner, set server-side |
| status | Select | | Active / Archived / Deleted |
| language | Select | | last detected |
| pinned_language | Select | | |
| message_count | Int | | |
| last_message_at | Datetime | | |
| summary | Long Text | | rolling summary for context trimming |
| total_input_tokens, total_output_tokens | Int | | |
| estimated_cost | Currency | | |

Naming: `hash` (random, non-guessable). Permissions: Chikku AI User → read/write/create **if_owner**; Chikku AI Manager → read (configurable; privacy decision Q6); **share disabled** (`allow_share`... implement `has_permission` hook: owner only, plus manager role if enabled). `track_changes` off.

## B4. Chikku AI Message

| Field | Type | Notes |
|---|---|---|
| conversation | Link Chikku AI Conversation ✓ | indexed |
| role | Select ✓ | user / assistant / tool / system_note |
| content | Long Text | plain text / markdown (assistant) |
| blocks_json | JSON | structured output blocks |
| language | Data | |
| modality | Select | text / voice / image / document |
| context_json | JSON | **validated** context only |
| attachments | Table → Chikku AI Message Attachment | |
| status | Select | Queued / Streaming / Done / Error / Cancelled |
| error_code | Data | |
| model_used | Link Chikku AI Model | |
| input_tokens, output_tokens | Int | |
| latency_ms | Int | |
| feedback | Select | 👍 / 👎 |
| feedback_note | Small Text | |
| idempotency_key | Data | unique per conversation (client-generated UUID for resend safety) |

Naming: `hash`. Permission: via `has_permission` hook → user can access only messages of conversations they own; `permission_query_conditions` → `conversation in (select name from tabChikku AI Conversation where user = %(user)s)`.

**Chikku AI Message Attachment** (child): `file` (Link File), `kind`, `handle` (att_1…), `parse_status`, `summary_json`, `warnings`.

## B5. Chikku AI User Preference

autoname `field:user`; fields: user (Link, unique), preferred_language, auto_detect_language (Check), response_style, greeting_enabled, voice_enabled, voice_name, speak_replies (Check), date_format_override. Permission: if_owner; never consulted for authorisation.

## B6. Chikku AI Action Request

| Field | Type | Notes |
|---|---|---|
| user | Link User ✓ | |
| conversation, message | Link | |
| action | Data ✓ | e.g. `draft.purchase_order.create` |
| target_doctype | Link DocType | |
| target_name | Data | for updates of drafts |
| params_json | JSON ✓ | canonical (sorted keys) |
| params_hash | Data ✓ | sha256(action + canonical params + user) |
| preview_json | JSON | what the user saw |
| token_hash | Data ✓ | sha256(token); token itself never stored |
| status | Select | Pending / Executing / Done / Failed / Expired / Cancelled |
| expires_at | Datetime ✓ | now + 10 min |
| confirmed_at, executed_at | Datetime | |
| result_doctype / result_name | Data | |
| error | Small Text | |
| risk_level | Select | low / medium (all drafts) |

Naming `hash`. Permission: owner read only; no write via UI (`read_only` DocType, writes through service with explicit `ignore_permissions` in `security/tokens.py`). Unique index on `token_hash`.

## B7. Chikku AI Tool Call

conversation, message, tool_name, args_json (**redacted**: no file contents, max 4 KB), result_summary (counts/refs only), status (ok/denied/error), denial_reason, duration_ms, rows_returned, user. Used for Activity Centre and debugging. Retention 90 days. Insert via `frappe.deferred_insert` to keep turn latency low (`frappe/deferred_insert.py` exists [V]).

## B8. Chikku AI Audit Log (append-only)

event_type (Select: write_executed, write_failed, permission_denied, token_replay, token_expired, injection_flag, file_rejected, settings_changed, provider_failover, budget_block, import_started, import_finished), user, ip, conversation, reference_doctype, reference_name, details_json (redacted), severity. Permissions: create only via code; read Chikku AI Manager / Auditor; **no write/delete** for anyone except System Manager purge job. Retention 2 years.

## B9. Chikku AI Usage Log

timestamp, user, conversation, provider, model, slot, input_tokens, output_tokens, cached_tokens, audio_seconds, characters (TTS), estimated_cost, latency_ms, status. Deferred insert. Aggregated by dashboards.

## B10. Chikku AI File Job

user, conversation, job_type (excel_generate, excel_modify, excel_compare, doc_extract, report_export), source_files (Table MultiSelect → File), output_file (Link File), status, progress, rq_job_id, parameters_json, error, started_at, finished_at.

## B11. Chikku AI Import Session (+ Mapping child, + Template)

Session: user, conversation, source_file, sheet_name(s), target_doctype, import_type, status (Draft / Mapping / Validated / Confirmed / Importing / Done / Partially Done / Failed / Cancelled), header_row, parent_key_column, child_table_field, `mappings` (Table → **Chikku AI Import Mapping**: source_column, target_field, target_table_field, transform (Select: none/trim/upper/date_format/number/lookup), transform_arg, confidence, is_mandatory, user_overridden), native_file (Link File: generated native-format XLSX), data_import (Link Data Import), row_count, success_count, error_count, errored_file, confirmation (Link Action Request), template (Link Import Template).

**Chikku AI Import Template**: name, target_doctype, header signature hash, mappings JSON, owner, shared (Check). Saves mappings for repeat imports.

## B12. Knowledge (R3)

- **Chikku AI Knowledge Collection**: title, description, roles (Table MultiSelect → Role) = who may query; enabled.
- **Chikku AI Knowledge Source**: collection, source_type (File / Web Page / Note / Wiki), file (Link File), status, checksum, last_indexed, chunk_count, error.
- **Chikku AI Knowledge Chunk**: source, collection (denormalised for filtering), seq, text (Long Text, FULLTEXT index), embedding (Long Text base64 float16 or JSON), page, heading. Not shown in UI.

## B13. Relationships

```
Settings ─< Role Capability
Provider ─< Model ─── (slots in Settings)
User ── Preference (1:1)
User ─< Conversation ─< Message ─< Message Attachment ── File
                    │           └─< Tool Call
                    ├─< Action Request ── (result) any DocType draft
                    ├─< File Job ── File(out)
                    └─< Import Session ─< Import Mapping ; ── Data Import ─< Data Import Log
Audit Log, Usage Log ── reference Conversation/User
Knowledge Collection ─< Source ── File ; Source ─< Chunk
```

## B14. Roles

| Role | Purpose |
|---|---|
| Chikku AI User | access the assistant |
| Chikku AI Manager | settings (except keys), dashboards, audit read |
| Chikku AI Auditor (optional) | read audit/usage only |
| Chikku AI Importer (R2, optional) | allowed to use conversational import (paired with Data Import perms; see Deliverable 10) |

Roles are created via `after_install`/patch **and** will appear in `fixtures/role.json` because `Role` is exported unfiltered (Deliverable 1 §3.2).

---

# Part C — API & Tool Registry Specification (Deliverable 8)

## C1. Whitelisted HTTP API (all `POST` unless noted, all require login + `Chikku AI User`, CSRF)

| Endpoint (`frappe_ai_assistant.frappe_ai_assistant.api.*`) | Purpose | Rate limit (per user) |
|---|---|---|
| `chat.open_session()` → {conversation, greeting?, capabilities} | panel open | 60/min |
| `chat.send_message(conversation, text, context, attachments[], idempotency_key, language?)` → {message_id} | enqueue turn | 20/min, 500/day |
| `chat.cancel_turn(message_id)` | stop generation | 30/min |
| `chat.list_conversations(cursor)` (GET) | history | |
| `chat.get_messages(conversation, cursor)` (GET) | | |
| `chat.rename / archive / delete_conversation` | | |
| `chat.feedback(message, value, note)` | | |
| `files.upload(conversation)` multipart | attach | 30/hour, size cap |
| `files.download(file)` (GET) | streams only files attached to caller's conversation or job | |
| `actions.confirm(action_request, token)` | execute draft | 30/min |
| `actions.cancel(action_request)` | | |
| `imports.update_mapping(session, mappings[])` | edit mapping | |
| `imports.validate(session)` | dry-run | 10/hour |
| `imports.start(session, token)` | confirmed import | 5/hour |
| `voice.transcribe(conversation)` multipart audio | STT | 60/hour |
| `voice.speak(message)` → audio file url | TTS | 60/hour |
| `admin.test_provider(provider)` (Manager) | connectivity test, never echoes key | 10/hour |
| `admin.usage_summary(...)` (Manager) | | |

Every endpoint: `@frappe.whitelist(methods=["POST"])`, typed arguments (`require_type_annotated_api_methods`-style), `@rate_limit`, capability check, settings `enabled` check.

## C2. Tool contract (common)

```python
class ToolSpec:
    name: str                    # "erp.search_documents"
    description: str             # model-facing
    input_model: type[BaseModel] # pydantic, extra="forbid"
    output_model: type[BaseModel]
    capability: str              # e.g. "read.erp"
    kind: Literal["read","draft","compute"]
    max_rows: int = 50
    audit: Literal["none","call","write"]
    def run(self, ctx: ToolContext, args) -> output_model
```

Guard steps for every call: (1) tool exists & enabled; (2) capability allowed for the user's roles; (3) pydantic validation (unknown keys rejected, strings length-capped, DocType names checked against `frappe.db.exists("DocType")` and Chikku **DocType denylist**); (4) run with user context; (5) cap & redact output; (6) log Tool Call; (7) wrap result as `<tool_result tool="…">` JSON for the model.

**Global DocType denylist for all tools** (beyond Frappe permissions): `User`, `Role`, `Has Role`, `DocPerm`, `Custom DocPerm`, `User Permission`, `Server Script`, `Client Script`, `System Settings`, `Chikku AI *` (except via dedicated tools), `Access Control`, `OAuth*`, `Social Login Key`, `Email Account`, `Integration Request`, `Webhook`, `Scheduled Job Type`, `Error Log`, `Activity Log`, `Version`, `Deleted Document`, `File` (only via file tools), all `__*` tables, all `*Settings` singles. Configurable additions.

Errors returned to the model as `{error_code, message}`: `NOT_FOUND_OR_NO_ACCESS` (never distinguish missing vs forbidden), `INVALID_ARGS`, `LIMIT_EXCEEDED`, `CAPABILITY_DISABLED`, `TRY_NARROWER_FILTER`.

## C3. Read tools (R1)

| Tool | Input | Output | Permission path | Notes |
|---|---|---|---|---|
| `erp.describe_doctype` | doctype | label, key fields (name, label, type, options, reqd), child tables, is_submittable, user perms {read, create, write} | `has_permission(doctype,"read")`; fields filtered by permlevel access | lets model build filters without guessing |
| `erp.search_documents` | doctype, filters[[field,op,value]] (ops ∈ =,!=,>,<,>=,<=,like,in,between,is), text?, fields[≤15], order_by (field + asc/desc), limit≤50 | rows, total_estimate, truncated, refs | `frappe.get_list` | fields validated against meta; no `*`; permlevel>0 fields removed if no access |
| `erp.get_document` | doctype, name, include_children(bool), child_limit≤200 | doc dict | `get_doc` → `check_permission("read")` → `apply_fieldlevel_read_permissions` | large text fields truncated |
| `erp.get_linked_documents` | doctype, name, linked_doctypes[] | {doctype: [refs+status]} | source read check; each list via `get_list` filtered by link field | own implementation (not `linked_with.get_linked_docs`) |
| `erp.count_documents` | doctype, filters | count | `get_list(..., fields=["count(name)"])` | |
| `erp.aggregate` | doctype, filters, group_by(field, ≤2), metrics [sum/avg/count/min/max of numeric field] | rows | `get_list` with aggregate fields + group_by | numeric fields only (Currency/Float/Int) |
| `stock.get_balance` | item_code, warehouse?, date? | qty, valuation? (if permitted) | `erpnext.stock.utils.get_stock_balance` (has its own checks) + Bin via `get_list` | |
| `stock.get_projected` | item_codes[≤50], warehouse? | Bin actual/reserved/ordered/projected | `get_list("Bin")` | |
| `party.get_summary` | party_type, party, company? | outstanding, billing this year, last transactions | `has_permission(party)` + `get_dashboard_info` | |
| `sales.order_status` | sales_order | per-item ordered/delivered/billed, linked DN/SI, stock vs pending | composite of above | J1 |
| `purchase.pending_items` | supplier?, purchase_order? | pending receipt qty per item | `get_list("Purchase Order Item")` via parent perms | |
| `accounts.invoice_payment_status` | doctype (SI/PI), name | outstanding, due date, payments (Payment Entry refs permitted) | get_list | |
| `reports.list` | text? | permitted reports (name, type, filters) | `get_list("Report")` + `frappe.has_permission("Report", doc=…)` | |
| `reports.run` | report_name, filters | columns, rows (cap 500), totals | `frappe.desk.query_report.run` as user | prepared reports → job + progress |
| `analytics.metric` | metric_id (registry: sales_total, purchase_total, receivables_aging, payables_aging, top_customers, top_items, stock_value, wo_status, delivery_delay), period, compare_period?, company?, group_by? | series, totals, method_note, source_refs | each metric = vetted service function over `get_list`/reports | **deterministic numbers**; formulas documented |
| `meta.explain_form` | doctype, fieldname? | field descriptions, mandatory rules, workflow states, next actions available (read-only) | read perm | ERP guidance |
| `workflow.state` | doctype, name | current state, possible transitions *for info* | `get_transitions` read-only | never applies |

## C4. Compute tools (no ERP writes)

| Tool | Purpose |
|---|---|
| `files.inspect` (att handle) | sheets/pages, headers, types, quality stats |
| `files.query_table` (att, sheet, filter/sort/group spec) | deterministic pandas-free table ops in Python |
| `excel.generate` (spec: sheets[{title, columns, rows_source: tool_result_ref, formats, charts, freeze, filters, validation}]) | XlsxWriter → File Job → file block |
| `excel.modify` (att, operations[]) (R2) | produces **new** file; original untouched |
| `excel.compare` (att_a, att_b, key_columns) | diff workbook |
| `docs.extract` (att, doc_type_hint?) (R2) | structured fields + confidence + validation issues |
| `knowledge.search` (query, collection?) (R3) | chunks with citations, permission-filtered |

`rows_source` references a cached tool result id: the model passes references, not raw numbers, so generated files contain server data verbatim.

## C5. Draft tools (R1.5+). All return `action_preview`, none writes

| Tool | DocType | Creates | Extra validation |
|---|---|---|---|
| `draft.sales_order` | Sales Order | new draft | customer/items/warehouse permitted; delivery_date ≥ transaction_date; backdate rule |
| `draft.quotation` | Quotation | new draft | |
| `draft.purchase_order` | Purchase Order | new draft | Amba PO custom fields (Server Script requirement) |
| `draft.material_request` | Material Request | new draft | |
| `draft.purchase_invoice_from_extraction` (R2) | Purchase Invoice | new draft | GSTIN match, totals match within tolerance, bill_no duplicate check |
| `draft.from_mapping` | DN/SI/PO from SO etc. | ERPNext mapper (`make_delivery_note`…) → draft | source permission; mapper output shown |
| `draft.update` | any enabled draft DocType | update fields on **docstatus=0** doc the user can write | field allowlist; never touches docstatus/workflow_state |
| `draft.add_comment` | Comment on doc | comment | |
| `draft.assign_todo` (R2) | ToDo | assignment | assignee must have read access |

Each draft tool spec defines `required_fields`, `resolvers` (Link fuzzy-match through `get_list` with `like`, returns candidates when ambiguous), and `preview_fields`.

**Not provided, ever:** submit, cancel, amend, delete, apply_workflow, set permissions, run report builder SQL, rename, merge, bulk update, email sending (R4 may add "draft email" as Communication draft only).

## C6. Audit requirements per tool kind

| Kind | Tool Call log | Audit Log | Usage |
|---|---|---|---|
| read | yes (args redacted, row count) | only on denial | — |
| compute | yes | file rejected / limits | — |
| draft prepare | yes | — | — |
| draft execute (confirm) | yes | **write_executed / write_failed** with doctype, name, params_hash | — |
| import start/finish | yes | yes | — |
