# Chikku AI — R0 + R1 Implementation Report

Date: 2026-11-03 · Site: `ambatest.local` (developer mode, test site) · App: `apps/amba_garments` (branch `test`)
Verified environment: Frappe 16.52.0, ERPNext 16.51.1, Python 3.14.6, Node 24.18.1 (nvm; the system `node` is 18 and too old for Frappe 16 builds), MariaDB 10.11, Redis 7.0, supervisor-managed gunicorn (3) + `short` and `long` RQ workers.

## A. What was built (status legend: ✅ implemented and tested · 🔑 implemented, awaiting credentials · 📐 prepared only · ⛔ not implemented)

| Capability | Status |
|---|---|
| `Chikku AI` module inside `amba_garments` (11 DocTypes + 2 child tables) | ✅ |
| Floating launcher on every Desk page + lazy-loaded Vue 3 chat panel | ✅ (rendering and logic tested in Node; **not visually checked in a browser**, since none is installed on the server) |
| Personal greeting (server-side first name, en / hi / Hinglish, once per tab session, never mid-conversation) | ✅ |
| Language detection + per-user preference (Auto/English/Hindi/Hinglish) | ✅ (heuristic detector; model quality per language still to be measured) |
| Conversations and messages, ownership enforced, pagination, history, new conversation | ✅ |
| Background turns in RQ (`long` queue), realtime streaming to the owner's room only | ✅ (live test through the real worker and socket.io) |
| Provider adapters: OpenAI-compatible (OpenAI, Gemini OpenAI endpoint, any compatible server) and Anthropic | ✅ over HTTP against a scripted local server · 🔑 **no live vendor call made (no API key on the server)** |
| Model slots (main, fallback, fast, vision, embedding, STT, TTS), retries, timeouts, circuit breaker, fallback | ✅ main + fallback used · 📐 other slots stored only |
| Desk context (open form, list selection), revalidated server-side | ✅ |
| Fixed tool registry with 8 read tools + 1 draft tool | ✅ |
| Draft-first framework (preview → token → confirm → revalidate → insert draft → verify → audit) | ✅ (Sales Order only; **Draft Actions are OFF by default**) |
| Amba business rules for drafts: 3-day backdate limit (bypass role "Backdate Approval"), Set Target Warehouse + Taxes mandatory (property setters), company-consistent warehouse/tax template | ✅ |
| Audit log, tool-call log, usage/cost log, admin usage summary, provider "Test Connection" button | ✅ |
| Per-user rate limits, daily token budget, monthly cost cap | ✅ |
| File attachments (private, type-checked, owner-bound) | ✅ storage + references · ⛔ content analysis |
| Excel, import, document intelligence, analytics metrics, knowledge/RAG, voice, workflow intelligence, proactive notifications | 📐 service contracts in `chikku_ai/extensions/` and disabled capability flags · ⛔ not implemented |
| Mic button | present but **disabled** ("coming soon") |

## B. Server changes

**Modified (existing files, additive only):**
- `amba_garments/hooks.py`: `app_include_js` += `chikku_ai.bundle.js`; `app_include_css`; `extend_bootinfo`; cron `*/10` expire action requests; `daily` retention purge; `has_permission`, `permission_query_conditions`, `after_migrate`. All existing hooks were kept.
- `amba_garments/modules.txt`: added `Chikku AI`.

**Created:**
- `amba_garments/patches.txt`: standard empty file. **Required**: Frappe's asset builder only recognises an app folder that has `patches.txt`. It references no patches; the existing `patches/` folder is untouched and still not run.
- `amba_garments/chikku_ai/` (backend, ~3,500 lines Python + tests):
  `__init__.py, boot.py, install.py, jobs.py, permissions.py`, `api/{chat,actions,files,admin}.py`, `ai/{orchestrator,prompts,language,greeting,render}.py`, `providers/{base,openai_compat,anthropic,router}.py`, `context/resolver.py`, `tools/{registry,erp_tools}.py`, `services/erp/{policy,query,business_rules,drafts}.py`, `security/{access,limits,tokens,untrusted,redaction}.py`, `audit/logger.py`, `extensions/__init__.py`, `doctype/*` (11 DocTypes + `private_document.py`), `tests/*` (Python + `tests/js`).
- Frontend: `public/js/chikku_ai.bundle.js` (launcher), `public/js/chikku_ai_panel.bundle.js`, `public/js/chikku_ai/{App.vue, components/*.vue, lib/{store,api,context}.js}`, `public/css/chikku_ai.bundle.css`, built output in `public/dist/`.

No Frappe/ERPNext core file was changed. No package was installed (`defusedxml` is not needed yet, because Excel parsing isn't implemented).

## C. DocTypes

| DocType | Type | Purpose | Who can read |
|---|---|---|---|
| Chikku AI Settings | Single | identity, greeting texts, language, model slots, limits, capability toggles, backdate rule, retention | System Manager, Chikku AI Manager |
| Chikku AI Provider Configuration | Normal | provider type, base URL, **API key (Password, permlevel 1)**, model, timeout, retries, pricing (child *Chikku AI Model Price*) | System Manager (key), Manager (no key) |
| Chikku AI Conversation | Normal (private) | one user's conversation | owner only |
| Chikku AI Message | Normal (private) | each user/assistant message, blocks, validated context, tokens/latency | owner only |
| Chikku AI User Preference | Normal | language, style, greeting | owner |
| Chikku AI Action Request | Normal, read-only fields | draft proposals, token hash, params hash, status, result | owner; Manager read |
| Chikku AI Tool Call | Log | tool, status, redacted args, rows, duration | Manager / System Manager |
| Chikku AI Usage Log | Log | provider, model, tokens, latency, cost estimate | Manager / System Manager |
| Chikku AI Audit Log | Log (append-only) | security and write events | Manager / System Manager |
| Chikku AI Suggested Prompt, Chikku AI Model Price | Child | | |

Roles created: **Chikku AI User**, **Chikku AI Manager**. Chikku records never send Frappe's site-wide `list_update` broadcast.

## D. API endpoints (`/api/method/frappe_ai_assistant.frappe_ai_assistant.api.<x>`; login + CSRF + Chikku access required)

| Endpoint | Purpose |
|---|---|
| `chat.open_session(conversation?)` | UI config, provider status, greeting (empty chat only), suggested prompts, history, messages |
| `chat.send_message(text, conversation?, context?, attachments?, idempotency_key?)` | stores message, enqueues turn; returns in < 0.5 s |
| `chat.get_messages(conversation, before_seq?, limit?)`, `chat.list_conversations()` | history with pagination |
| `chat.create_conversation`, `rename_conversation`, `archive_conversation`, `cancel_turn(message)`, `set_language`, `set_feedback` | |
| `files.upload(conversation?)` (multipart) | private attachment |
| `actions.confirm(action_request, token)`, `actions.cancel`, `actions.reissue`, `actions.status` | draft confirmation |
| `admin.test_provider(provider)`, `admin.usage_summary()` | managers only |

Realtime event `chikku_ai_event` (user room only): `status`, `delta`, `tool`, `action_token` (browser only), `done`, `error`, `action_done`.

## E. Frontend

`chikku_ai.bundle.js` (2 KB) loads on every Desk page for permitted users (`frappe.boot.chikku_ai`) and draws the launcher bottom-right (z-index below Frappe dialogs; hidden on print). The first click loads `chikku_ai_panel.bundle.js` (92 KB). The panel has a header with avatar, name, status, language switch, history, new chat, expand and close; a context chip; the composer with attach, (disabled) mic and send/stop; and rich blocks for document cards, tables, charts and draft previews. Assistant HTML is sanitised on the server (markdown2 escape mode + nh3 allow-list); user text is never rendered as HTML. `Esc` closes the panel; controls have ARIA labels; on screens narrower than 640 px it opens full-screen.

## F. AI providers

| Adapter | Tested |
|---|---|
| OpenAI-compatible (OpenAI, Google Gemini via OpenAI endpoint, other compatible servers) | ✅ streaming, tool calls, usage, retries, 401/429/5xx/timeout/malformed, over real HTTP to a local scripted server; ✅ live end to end through the worker |
| Anthropic Messages API | ✅ streaming text + tool_use, wire format, over real HTTP to a local scripted server |
| Real vendor endpoints | 🔑 **not tested: no API key available on the server** |

## G. Security mechanisms

- Access: role gate + kill switch + per-capability toggles; Website users never.
- Data: all reads via `frappe.get_list` / `check_permission` + `apply_fieldlevel_read_permissions`; a denylist of ~60 system/security DocTypes; a CI lint test forbids `get_all`, `db.sql` and `ignore_permissions` in tools/services.
- Company: aggregates require an explicit company filter; User Permissions are honoured (tested with a Company User Permission). **The site still has 0 User Permissions, so users see both companies until these are configured.**
- Privacy: conversations/messages owner-only (manager included); no `list_update` broadcasts; realtime to user room only.
- Model containment: tool results and ERP context wrapped as `<untrusted_data>`; injection phrases flagged in audit; no SQL/code/shell/generic-method tools; tool identity must equal the turn owner.
- Drafts: token 256-bit, shown only to the browser, stored as `sha256(token|params_hash)`, bound to user + action + canonical params, 10-min expiry, atomic single use, revalidated at confirm, insert as user, docstatus 0 verified. Never submit/cancel/delete/workflow.
- Secrets: Password field (encrypted), never in boot/API/realtime/logs; redaction in errors and logs.
- Abuse: per-user rate limits (Redis), token/cost budgets, upload type/size/magic checks, macro files rejected.
- Durable audit: High/Critical events are written via a short job, so they survive request rollbacks.

## H. Tests actually executed (final run, after all fixes)

| Suite | Command | Result |
|---|---|---|
| Python (unit + security + integration on the site, one rolled-back transaction) | `bench --site ambatest.local execute frappe_ai_assistant.frappe_ai_assistant.tests.runner.run` | **82 / 82 passed**; 0 test records left |
| Frontend (store logic, context, Vue SSR rendering) | `node apps/amba_garments/amba_garments/chikku_ai/tests/js/run.js` (Node 24) | **29 / 29 passed** |
| Live integration (real login, Desk HTML, CSRF, RQ worker, socket.io, scripted provider, draft confirm, replay) | scratch driver (not shipped) | **27 / 27 passed**; all created data removed |
| Existing Amba features (barcode, PO tracking report) | earlier regression script | **20 / 20 passed** |

Why not `bench run-tests`: it creates permanent `_Test …` fixture records in this site's real database.

## I. Deployment performed on `ambatest.local`

Backup `sites/ambatest.local/private/backups/20261103_225433-*` + app archive `/home/frappe/chikku_recovery_20261103_172431/`. Then: `bench migrate` (×3, including after DocType refinements), `bench build --app amba_garments` (Node 24), `bench clear-cache`, `supervisorctl restart` web + workers (×3). Redis and socket.io were not restarted. Production was not touched.

## J. To activate (administrator)

1. **Chikku AI Provider Configuration → New**: type, default model, API key → *Test Connection*.
2. **Chikku AI Settings → AI Models**: Main Provider (optional Fallback).
3. Assign role **Chikku AI User** to users (System Managers already have access); **Chikku AI Manager** for admins.
4. Optional: model prices (for cost estimates), limits, greeting texts, suggested prompts.
5. Only after review: **Draft Actions Enabled** (Sales Order drafts).
6. Before production: configure **User Permissions** on Company for users who must not see the other company.

## K. Limitations / follow-ups

- No live AI vendor tested (no key). Hinglish quality untested with a real model.
- UI not checked visually in a browser (no browser on the server); please open Desk and try it.
- Only one draft action (Sales Order). The naming series on this site produced `SO--000NN` (company abbreviation missing from series `SO-.abbr.-`); that is existing site configuration, so check it.
- Turns run on the shared `long` worker (1 vCPU, 1.9 GB RAM host): fine for a pilot, but a dedicated worker and a bigger server are needed for production.
- Capabilities marked 📐/⛔ above.
- The test-site log shows the existing `user_restriction` login code writing ~10 debug Error Logs per login (pre-existing, unrelated).

## L. Verification evidence

From the live integration run: Desk HTML contains `/assets/amba_garments/dist/js/chikku_ai.bundle.XYEJTFAR.js`, the CSS bundle, and still `restrict_backdate.js` and `barcode_print.js`; boot exposes only `{"enabled","assistant_name","avatar"}`; greeting "Hey Kamal! How's your day going? …"; `send_message` returned in 0.37 s; the worker finished the 3-round tool turn in 7.6 s; socket.io delivered `status, tool, delta, action_token, done`; confirm created draft Sales Order docstatus 0; replay → "This action was already handled."

## M. Recovery / rollback

- Instant: untick **Assistant Enabled** in Chikku AI Settings (launcher disappears, APIs refuse).
- Code: restore `hooks.py` from `/home/frappe/chikku_recovery_20261103_172431/hooks.py.before_chikku` (it also contains the barcode hooks added earlier), remove `Chikku AI` from `modules.txt`, delete `amba_garments/chikku_ai/` and the Chikku frontend files, then `bench build --app amba_garments`, `bench --site ambatest.local migrate` and restart. Chikku DocTypes can stay (harmless), or be removed from Desk.
- Full: restore DB backup `20261103_225433` and the app archive.
