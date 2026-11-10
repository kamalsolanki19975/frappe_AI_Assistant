# Deliverables 2, 9, 12 — Product Requirements, Multimodal Specification, UI/UX Specification

---

# Part A — Product Requirements Document (Deliverable 2)

## A1. Product statement

Chikku AI is one configurable assistant embedded in ERPNext Desk. It answers questions about authorised ERP data, analyses records and files, and prepares **draft** documents, workbooks and imports. It never submits, cancels, deletes or advances workflows. Every action is executed by the server as the signed-in user, through a fixed tool registry.

## A2. Actors

| Actor | Description | Typical roles on this site |
|---|---|---|
| Employee | Day-to-day operator (sales, purchase, store, production, accounts) | Sales User, Purchase User, Stock User, Manufacturing User, Accounts User |
| Manager | Reviews, analyses, approves through native workflows | *Manager roles, workflow approvers |
| Chikku Administrator | Configures assistant, providers, capabilities, limits; sees usage and audit | new role `Chikku AI Manager` (+ System Manager) |
| System Manager | Owns ERPNext config, User Permissions, Data Import | System Manager |
| Auditor (optional) | Read-only access to Chikku audit logs | new role `Chikku AI Auditor` |
| AI provider | External processor (LLM, STT, TTS, embeddings) | — |
| Scheduler/worker | Executes jobs **as the requesting user** | — |

Access to Chikku itself is gated by role `Chikku AI User` (System Manager implied). Capabilities can be toggled per role (§A5).

## A3. Capability map and release placement

| # | Capability | Release | Notes |
|---|---|---|---|
| 1 | Intelligent ERP assistant (Q&A over records) | **R1** | read tools |
| 2 | Context-aware ERP intelligence (current form/list) | **R1** | |
| 3 | Multimodal input: text, attachments, images | **R1** | voice push-to-talk R2 |
| 4 | Excel: analyse + generate | **R1** | modify/regenerate R2 |
| 5 | Conversational data import (via native Data Import) | **R2** | needs Data Import permission decision |
| 6 | Business intelligence and analytics | R1 (predefined metrics) → R3 (anomalies) | |
| 7 | Document intelligence (invoice/challan extraction → draft) | **R2** | |
| 8 | Company knowledge assistant (RAG) | **R3** | needs embedding provider; vector store decision |
| 9 | Voice: push-to-talk | **R2**; continuous voice **R4** | |
| 10 | Workflow intelligence (proposals only) | **R3** | never activates |
| 11 | ERP learning and contextual guidance | R1 (explain form/field), R3 (guided tours) | |
| 12 | Proactive intelligence and notifications | **R4** | scheduler-driven, opt-in |
| 13 | AI Activity Centre (user view) | R1 basic → R2 full | |
| 14 | Admin monitoring and governance | R1 basic (usage, audit) → R2 dashboards | |

Draft writes (Sales Order, Purchase Order, Quotation, Material Request) arrive in **R1.5**, after the read path has been in production with audits for at least 2–4 weeks.

## A4. Key user journeys (with acceptance criteria)

**J1 — Ask with context (R1).** On Sales Order `SAL-ORD-2026-00123` the user asks "Why is this order still pending?"
- Server re-reads the SO as the user (permission check), plus permitted Delivery Notes, Sales Invoices, Bin quantities, workflow state.
- Answer cites each record as a clickable document card; numbers come from tool outputs, never from the model.
- AC: answer ≤ 15 s p90; every record mentioned is one the user can open; if a linked DocType is not permitted, the answer says "some related records aren't visible to you" without naming them.

**J2 — Compare months (R1).** "Compare this month's sales with last month." Date ranges are resolved server-side in `Asia/Kolkata` and shown to the user ("1–31 Oct 2026 vs 1–30 Sep 2026"). The `sales_summary` metric tool aggregates submitted Sales Invoices (docstatus=1, excluding returns unless asked) per company. The reply has a table, chart and narrative, plus an offer to download XLSX. AC: totals reconcile to the *Sales Register* report for the same filters within ₹0.01.

**J3 — Prepare a draft PO (R1.5).** "Make a PO to Shree Textiles for 500 m of 60s cotton at last rate." Supplier and item resolve with disambiguation if fuzzy, last rate comes from a permitted tool, and required custom fields (`custom_po_processing_owner` etc.) are asked for. A preview card shows the full document and a **Create draft** button. The server revalidates, inserts with `docstatus=0` and returns a link. AC: no insert without a confirmation token; replaying the token fails; the PO appears in the workflow's initial state.

**J4 — Analyse a workbook (R1).** User uploads `stock_oct.xlsx`. Chikku lists sheets, detects headers and types, reports duplicates and blanks, and answers questions. AC: macro files rejected; files over the limit rejected with a clear message; formulas are never evaluated (cached values only).

**J5 — Conversational import (R2).** "Import these items." Chikku proposes the DocType and mappings, the user corrects them in a mapping grid, Chikku runs a dry-run preview through the native importer, the user confirms, and the native import runs in the background with progress. Errored rows are offered as an XLSX for retry. AC: `submit_after_import` is 0 on every Chikku-created Data Import; the user needs `import` + `create` permission on the target.

**J6 — Supplier invoice to draft Purchase Invoice (R2).** User drops 3 PDFs. Each is classified and fields extracted with per-field confidence; supplier is matched by GSTIN, PO/PR matched, arithmetic and tax checked. A side-by-side review appears and draft PIs are created only after confirmation. AC: no field shown as certain if confidence < threshold; totals mismatch blocks one-click creation.

**J7 — Voice query (R2).** User holds the mic and speaks "Rohit Traders ka outstanding kitna hai?". The editable transcript appears and is sent; the answer comes in Hinglish text with optional spoken reply. AC: identical permission path to text; audio not retained beyond the configured period (default 0 days, i.e. delete after transcription).

**J8 — Knowledge question (R3).** "What is the leave encashment policy?" Answer comes only from documents in collections the user may read, with source citations. AC: revoking access removes the document from results within 5 minutes.

**J9 — Workflow proposal (R3).** Admin describes a rule. Chikku produces a reviewable proposal (Notification / Assignment Rule / Workflow JSON + plain explanation + conflict check). Nothing is saved until the admin clicks "Create as disabled" and then enables it manually in native UI.

## A5. Functional requirements (abridged, numbered for traceability)

**Assistant and settings**
- FR-1 Single `Chikku AI Settings` (Single DocType): identity, greeting, capabilities, default language, limits.
- FR-2 Provider credentials stored only in Password fields; never returned to the client.
- FR-3 Capability toggles per role (child table `Chikku AI Role Capability`).
- FR-4 Kill switch: disabling Chikku hides the launcher and rejects all API calls within one request.

**Conversation**
- FR-10 Conversations are owned by one user and never shared (sharing disabled on the DocType).
- FR-11 New / rename / archive / delete own conversation; delete = soft-delete with retention purge.
- FR-12 Streaming responses with stop button.
- FR-13 Message types: text, table, chart, document-card, file, action-preview, import-mapping, progress, error.

**Context**
- FR-20 Client sends route context `{route, doctype, docname, list_filters, selected_names[≤50]}`; server revalidates every identifier.
- FR-21 Context is attached per message (not per conversation), so switching forms doesn't leak earlier context.

**Tools**
- FR-30 Only registered tools; every call is schema-validated (pydantic), permission-checked, audited.
- FR-31 Read tools return capped results (default 50 rows, hard max 500), include `truncated` flags and record references.
- FR-32 Draft tools produce an `Chikku AI Action Request` + preview; execution only via a confirm endpoint with token.
- FR-33 No tool can submit, cancel, amend, delete, apply workflow actions, change permissions, run SQL or code.

**Language**
- FR-40 Detect English / Hindi (Devanagari) / Hinglish (Romanised) per message; reply in kind unless the user pinned a language.
- FR-41 Never translate identifiers: item codes, document names, party names, field values in tables.

**Files**
- FR-50 Allowed types R1: `.xlsx .csv .pdf .png .jpg .jpeg .webp .docx .txt`; `.xls` read-only via xlrd; reject `.xlsm .xlsb .xltm .docm`, archives, executables.
- FR-51 Uploads stored as private `File` attached to the conversation; downloads only via permission-checked endpoint.

**Excel, import, documents, analytics, knowledge, voice, workflow, activity centre:** see Deliverables 9, 10 and the tool registry (Deliverable 8).

## A6. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | First token latency (text, R1) | p50 ≤ 3 s, p90 ≤ 6 s (provider-dependent) |
| NFR-2 | Full answer, tool-using question | p90 ≤ 20 s; hard turn timeout 120 s |
| NFR-3 | Web worker occupancy | No LLM call inside a gunicorn request; turns run in RQ |
| NFR-4 | Concurrency (current hardware) | ≤ 2 concurrent turns; queue beyond that with a "waiting" state |
| NFR-5 | Availability | Chikku failure must never break Desk; launcher degrades to "unavailable" |
| NFR-6 | Security | All OWASP ASVS L2 controls relevant to the API; see Deliverable 11 |
| NFR-7 | Privacy | Configurable retention: messages 180 d, files 30 d, audio 0 d, audit 2 y (defaults, admin-adjustable) |
| NFR-8 | Cost control | Per-user daily token budget and site monthly budget, with hard stop |
| NFR-9 | Accessibility | Keyboard reachable, ARIA live region for streaming, WCAG 2.1 AA contrast |
| NFR-10 | Maintainability | ≥ 80 % unit test coverage on `security/`, `tools/`, `import/` |
| NFR-11 | Portability | Swapping provider = settings change; no ERP service code changes |

---

# Part B — Multimodal Interaction Specification (Deliverable 9)

## B1. Normalisation pipeline

All inputs become one internal `TurnInput`:

```
TurnInput {
  conversation, user (from session only), text, language_hint,
  attachments: [AttachmentRef{file_name(File.name), kind, parsed_summary_ref}],
  context: ValidatedContext{doctype, docname, route_kind, list_filters, selected[]},
  modality: "text" | "voice" | "image" | "document"
}
```

```
voice ─► STT ─► editable transcript ─┐
text ───────────────────────────────┤
image ─► store private File ─► (vision part) ┤──► TurnInput ──► Orchestrator ──► Tools (same guard for all)
pdf/docx/xlsx/csv ─► store ─► parse job ─► summary + handles ┘
ERP context ─► server revalidation ─┘
```

**There is exactly one orchestrator and one tool guard.** Voice, image and document inputs only produce *text and data handles*; they cannot call tools directly.

## B2. Per-modality handling

| Modality | R | Processing | Limits (defaults) | Untrusted-content rule |
|---|---|---|---|---|
| Text | R1 | Unicode NFC normalise; strip control chars; detect script (Devanagari range U+0900–U+097F) + Hinglish heuristic (Latin script with Hindi lexicon hits) + model fallback | 8,000 chars/message | User text is the only instruction channel |
| Pasted tables | R1 | Detect TSV/markdown tables → parse to rows | 2,000 rows | data |
| Images | R1 | Pillow: verify, strip EXIF, downscale to ≤ 2048 px long edge, re-encode; send to vision-capable model | 10 MB, 8 per message | Text in images = data, wrapped in `<untrusted_document>` |
| PDF | R1 | pdfplumber text + tables; if text layer < N chars/page → rasterise with pypdfium2 (150 dpi) and use vision | 25 MB, 50 pages | data |
| DOCX | R1 | zipfile + lxml read `word/document.xml` (no python-docx needed); reject `.docm` | 10 MB | data |
| XLSX/CSV | R1 | See Deliverable 10 | 15 MB, 200k cells read | data |
| Voice (push-to-talk) | R2 | Browser `MediaRecorder` (webm/opus) → upload → STT provider → transcript shown editable | 60 s/clip | transcript is user text **after** the user sees/edits it |
| Voice (continuous) | R4 | WebSocket to STT streaming via a server relay; VAD turn detection; barge-in stops TTS | session ≤ 10 min | same |
| ERP context | R1 | §B3 | ≤ 50 selected names | — |

**Multiple attachments.** Each is a separate `File` with `attached_to_doctype = "Chikku AI Conversation"` and `attached_to_name = conversation`. A `Chikku AI Message Attachment` child row links message → file → parse status. Reconciliation tasks (two workbooks, PO vs invoice) reference attachments by handle (`att_1`, `att_2`) in tool calls. The server maps handles to File names owned by the same conversation and user.

## B3. ERP context handling

1. Client builds context from `frappe.get_route()` and `cur_frm`/`cur_list` **only as hints**: `{route:["Form","Sales Order","SAL-ORD-…"], list_filters:[…], selected:[…]}`.
2. Server `context.resolve()`:
   - parses route kind (Form / List / Report / Workspace / Page);
   - for Form: `frappe.has_permission(doctype, "read", docname)` → else drop the context silently and tell the model "no accessible document in context";
   - for List: re-applies filters through `frappe.get_list` (never trusts counts sent by the client);
   - selected names: each re-checked; non-permitted dropped;
   - builds a **compact context card** (key fields only, through field-level perms) for the prompt.
3. Context freshness: the card carries `modified` timestamp; tools re-read the DB, so the model never relies on a stale card for numbers.
4. Isolation: context is stored on the **message**, not the conversation, and expires with it.

## B4. Output selection

The orchestrator returns structured *blocks*; the model chooses blocks through tools, not by writing HTML:

| Block | Produced by | Rendering |
|---|---|---|
| `markdown` | model text | `frappe.markdown` → sanitised (DOMPurify-equivalent allowlist via frappe's `frappe.dom` / server `sanitize_html`) |
| `table` | read/analytics tools | Vue table, numbers formatted with user's number format |
| `chart` | analytics tools (spec only: type, labels, datasets) | frappe-charts |
| `doc_card` | any tool returning record refs | link to `/desk/<doctype>/<name>` |
| `file` | excel/report generators | download via `/api/method/frappe_ai_assistant.frappe_ai_assistant.api.files.download?name=` |
| `action_preview` | draft tools | read-only form-like preview + Confirm/Cancel |
| `import_mapping` | import tools | mapping grid |
| `progress` | background jobs | realtime progress bar |
| `error` | guard/tools | friendly text + reference id |

Numbers in prose must come from tool output: the system prompt requires the model to quote tool numbers. A post-check flags any number in the final answer that appears in no tool output, and shows a "verify" hint (R2).

## B5. Language specification

- Detection order: user's pinned preference → script detection → Hinglish classifier (lexicon of ~2,000 common Romanised Hindi tokens; ratio threshold) → model self-report.
- Reply language = detected language; Hinglish replies in Roman script, Hindi in Devanagari.
- Identifier protection: tool outputs are rendered in tables untouched; the system prompt forbids translating values in backticks/tables; a post-check confirms every document name in the answer exists verbatim in the tool output.
- Bilingual Excel headings: generator accepts `heading_lang = en | hi | en+hi`; Hindi labels come from an editable translation map (Frappe's `hi` translations are not enabled on the site), never machine-translated at runtime for field labels.
- **Evaluation:** build 3 separate test sets (English, Devanagari Hindi, Hinglish), ~150 prompts each, covering ERP Q&A, numbers, dates ("pichhle mahine"), and names. Hinglish is scored separately because Hindi capability does not imply natural Hinglish.

## B6. Greeting lifecycle

- Trigger: the **first open of the panel per browser tab session** where the active conversation has no messages. The server creates the greeting on `chikku.open_session()`; `sessionStorage` flag `chikku_greeted:<user>` prevents repeats across re-renders.
- First name: server-side `frappe.db.get_value("User", frappe.session.user, "first_name")`; fallback to `full_name` first token, then the localised word for "there"/"ji". Never from the client.
- Templates in settings for en / hi / hinglish, with `{first_name}` and `{assistant_name}` only (no other placeholders, Jinja disabled → no template injection).
- The greeting is not sent to the LLM (static template), so it costs nothing and is not shown as an LLM message.
- Never inserted into an existing conversation; opening the panel mid-conversation restores that conversation instead.

---

# Part C — UI/UX Specification (Deliverable 12)

## C1. Integration method (verified)

- `hooks.py`: `app_include_js` += `"chikku_ai.bundle.js"`, `app_include_css` += `"chikku_ai.bundle.css"` (esbuild bundle, same pattern as `saarthi_home`). Source in `amba_garments/public/js/chikku_ai/` with `chikku_ai.bundle.js` entry.
- Mounted once on `frappe.ready`/`$(document).ready` into `<div id="chikku-ai-root">` appended to `body`; **Vue 3** (shipped with frappe) for the panel; lazy-load the heavy panel bundle on first click so Desk start-up cost is ~5 KB.
- Bootstrap data (assistant name, avatar, enabled, capabilities for this user) via `extend_bootinfo` hook, no extra request on load. Secrets are never put in boot.
- Route tracking via `frappe.router.on("change")`; form context via `$(document).on("form-refresh")`.
- Realtime via `frappe.realtime.on("chikku_ai_stream", …)` (socket.io, user room).

## C2. Layout

```
Desktop (≥ 992 px)                         Mobile (< 768 px)
┌──────────────────────────── Desk ───┐    ┌──────────────┐
│                                     │    │ ◀ Chikku AI ⋮│ full-screen sheet
│                     ┌─────────────┐ │    │ messages      │
│                     │ 🟣 Chikku AI │ │    │               │
│                     │ ─ ⤢ ✕       │ │    │               │
│                     │ [history ▾] │ │    │ [📎][🎤][ ➤ ] │
│                     │ messages…   │ │    └──────────────┘
│                     │ cards/tables│ │
│                     │─────────────│ │
│                     │📎 🎤 EN▾ [➤]│ │
│                     └─────────────┘ │
│                                (◉) │ ← launcher, bottom-right, 56 px
└─────────────────────────────────────┘
```

- Panel 420 × min(720, 85vh); expand button → 760 px wide "focus" mode; history drawer on the left.
- z-index below Frappe modals (`.modal` 1050) so dialogs stay on top; launcher hidden on `/desk/print` and print views.
- Respects Desk dark/light theme via CSS variables (`--bg-color`, `--text-color`).

## C3. Components

| Component | Behaviour |
|---|---|
| Launcher | Avatar + optional unread dot (R4 proactive). `Alt+K` toggles. Tooltip with assistant name. |
| Header | Name, status (Ready / Busy / Offline (provider down) / Disabled), language menu (Auto, English, हिन्दी, Hinglish), new chat, expand, close. |
| Context chip | "📄 Sales Order SAL-ORD-…" above composer; ✕ to exclude context from the next message. |
| Composer | Multiline, Enter to send, Shift+Enter newline, paste-table detection, drag-drop files, attachment chips with parse status, mic button (hold or toggle). |
| Message list | Virtualised after 200 messages; streaming with ARIA `aria-live="polite"`; stop button during generation. |
| Suggested prompts | From settings, filtered by current route DocType. |
| Action preview | Header "Draft — not yet saved", field table, child-table grid, warnings, **Create draft** (primary) / **Edit** / **Cancel**; expiry countdown; after creation shows doc link + status. |
| Import mapping | Grid: Source column · Sample values · Target field (searchable select limited to importable fields) · Confidence · Required marker; unmapped mandatory fields highlighted; "Validate (dry run)" → row errors table; "Start import" disabled until zero blocking errors. |
| Progress | Bar + ETA from realtime; cancel button where supported. |
| Voice | Hold-to-talk; waveform; transcript appears editable before send; speaker toggle for spoken replies. |
| Error states | Provider unavailable (retry in Ns), rate limited, file rejected (reason), permission denied (neutral wording), network lost (auto-reconnect, resend unsent draft from local state). |

## C4. AI Activity Centre

- **User view**: a Desk page `/desk/chikku-activity` with tabs: Conversations · Files · Drafts created · Pending confirmations · Imports · Jobs · Errors. It is built on `frappe.get_list` over Chikku DocTypes filtered to `owner = session user`.
- **Admin view** (role `Chikku AI Manager`): Usage (tokens, cost estimate by provider/model/day/user), Latency p50/p90, Tool failure rate, Security events (permission denials, injection flags, replay attempts), Import activity, Queue health. It uses Number Cards + Dashboard Charts over `Chikku AI Usage Log` and `Chikku AI Audit Log`, so it is native and needs no custom charting backend.

## C5. Safety in rendering

- Never render model-supplied raw HTML. Markdown → HTML → sanitise with an allowlist (p, ul, ol, li, strong, em, code, pre, table elements, a[href] with `http(s)` and `/desk/` only).
- Links to documents are built by the client from `{doctype, name}` refs, not from model text.
- Charts receive JSON specs validated server-side (type ∈ {bar, line, pie, percentage}, ≤ 12 datasets, ≤ 500 points).
