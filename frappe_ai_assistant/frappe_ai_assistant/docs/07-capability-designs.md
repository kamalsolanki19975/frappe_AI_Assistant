# Capability Designs — Analytics, Document Intelligence, Knowledge, Voice, Workflow, Proactive, Learning

These complete Deliverables 6 and 9 for the capabilities not covered elsewhere.

## 1. Business intelligence and analytics (Part 11 of the brief)

**Approach: a vetted metric registry + permitted reports. No LLM-generated SQL.**

- `services/erp/metrics.py` defines each metric as a function with a documented formula, e.g.:
  - `sales_total(period, company, group_by)`: Σ `base_net_total` of Sales Invoice, `docstatus=1`, `is_return=0` (returns optional), posting_date in period, via `frappe.get_list(..., fields=["sum(base_net_total) as v", group_by])`.
  - `receivables_aging(company, as_of)`: runs ERPNext *Accounts Receivable* report via `query_report.run` as the user (respects report and DocType perms).
  - Similar: purchase_total, payables_aging, top_customers, top_items, stock_value (Stock Balance report), wo_status, delivery_delay (SO delivery_date vs DN posting_date), plus Amba reports (e.g. *Sales Order Delivery Invoice Tracker*, *Purchase Order Tracking Report*) registered as report-backed metrics.
- Date phrases ("this month", "pichhle mahine", "last FY") resolved by deterministic code in `Asia/Kolkata`, with the Fiscal Year from ERPNext; the resolved range is echoed to the user.
- Output: `{series, totals, method_note, filters_used, source_refs (sample doc names), generated_at}` → table + chart blocks; the model writes the narrative only from these values.
- Reproducibility: each analytics answer stores `filters_used` + metric version in the Tool Call, so it can be re-run.
- Performance: aggregates run in the worker; results cached in Redis per (user, metric, args) for 5 min; large reports use prepared reports (async) with progress.
- Anomaly detection (R3): rule-based on metric series (z-score > 3, IQR, period-over-period > ±40 %), no ML service.
- Script/Query Reports: only run, never created or edited by Chikku. Query Reports contain admin-written SQL; running them as the user is the native behaviour (`is_permitted()` checks report roles).

## 2. Document intelligence (Part 12)

Pipeline per attachment:

```
classify (fast slot, first page text/image) → doc_type ∈ {supplier_invoice, purchase_order, delivery_challan,
   bank_statement, expense_receipt, quotation, other}
extract (vision_extract slot, JSON schema per doc_type, each field {value, confidence, source_page, bbox?})
validate (deterministic):
   GSTIN format + checksum; PAN pattern; date sanity; Σ(line qty×rate) ≈ taxable; taxable + CGST+SGST|IGST ≈ total;
   intra/inter-state vs place of supply (India Compliance helpers where available); HSN format
match (read tools, as user): Supplier by GSTIN → name fuzzy; Items by supplier part no / item name;
   PO/PR candidates by supplier + items + open qty; duplicate bill_no check
present: side-by-side (page image | fields) with confidence colours; uncertain fields (conf < 0.8 or failed
   validator) must be confirmed/edited by the user
draft: draft.purchase_invoice_from_extraction → Action Request → confirm
```

Provider options:

| Option | Hindi/English | Tables | Privacy | Cost | Verdict |
|---|---|---|---|---|---|
| Multimodal LLM (same provider as reasoning) | good (measure) | good | provider DPA | per page tokens | **R2 default** |
| Google Document AI / Azure Document Intelligence | strong English; Hindi varies by model | strong | cloud, regional options | per page | alternative for high volume (POC) |
| Sarvam Vision | Indic-focused [D] | unknown | India-hosted | — | POC candidate |
| Tesseract (hin+eng) | weak on Hindi per public tests [D] | weak | local | free | **NR** |
| PaddleOCR self-hosted | supports Devanagari [D] | ok | local | infra | **NF** on current host |

Never fabricate: the extraction schema allows `null`; the prompt requires `null` + `confidence: 0` when not visible; validators override model confidence.

## 3. Company knowledge assistant (Part 13)

- Sources: Files uploaded to a **Chikku AI Knowledge Collection** (R3), optionally Wiki/Web Pages later.
- Chunking: by headings/paragraphs, ~800 tokens, 100 overlap; keeps page and heading for citations.
- Index: MariaDB table (Chunk DocType) with **FULLTEXT** index (keyword/BM25-like) + stored embedding vector (float16 base64) from the `embedding` slot.
- Retrieval: (1) collection filter = collections whose `roles` intersect the user's roles **and** source File readable; (2) FULLTEXT top 50 + cosine re-rank in Python over those candidates (no full-table vector scan needed); (3) top 6 chunks to the model with citations `[S1]` → rendered as links to the source file.
- Permission/revocation: chunks carry `collection`; checks happen at query time, so revoking a role or disabling a source has immediate effect; deletion of a source deletes chunks (`on_trash`).
- Incremental indexing: checksum on File content; re-index on change; scheduler job for pending sources.
- Scale decision: up to ~20k chunks this design is adequate on MariaDB 10.11 [A, POC]. Beyond that: upgrade to **MariaDB 11.8 LTS** (native `VECTOR` + HNSW index [D]) rather than adding a separate vector DB. Upgrading MariaDB affects all of ERPNext and needs its own project.
- Hindi: FULLTEXT tokenises on whitespace, which suits Devanagari words; Hinglish queries are matched to Hindi docs mainly via embeddings, so the embedding model must be chosen on a Hindi/Hinglish retrieval test.

## 4. Voice (Part 14)

**Push-to-talk (R2)**
```
mic (MediaRecorder webm/opus, ≤60 s) → api.voice.transcribe (multipart, rate-limited)
 → File(private, kind=audio) → STT slot (Sarvam saaras / fallback) with language hint (hi-IN/en-IN, code-mix)
 → transcript returned → shown editable in composer → user sends → normal text path
 → audio deleted immediately when retention_audio_days = 0
optional reply audio: api.voice.speak(message) → TTS slot → private File (short-lived) → <audio> playback
```
- Consent: first use shows a notice ("Your voice is sent to <provider> for transcription; not stored"); acceptance stored in User Preference.
- Browser: MediaRecorder is supported in Chrome/Edge/Firefox/Safari (recent); requires HTTPS (or localhost).
- Browser Web Speech API **not recommended** as primary: Chrome sends audio to Google without enterprise terms, and support is missing/limited in Firefox.

**Continuous conversation (R4)**: requires low-latency bidirectional streaming. Options: (a) a small separate async relay service (e.g. a uvicorn/websockets process; `websockets` 15 is already installed) authenticated with a short-lived Frappe-issued token, proxying to provider streaming STT/TTS; (b) provider-direct WebRTC/WebSocket with ephemeral client tokens minted by Frappe (where the provider supports it). Either way, recognised text goes through the same `send_message` path; tool execution and confirmations stay in Frappe. Turn detection via provider VAD; barge-in = stop TTS playback + cancel turn.

## 5. Workflow intelligence (Part 15)

- Capability for `Chikku AI Manager` + System Manager only.
- Inputs: natural-language rule. Chikku reads existing Workflows, Notifications, Assignment Rules, Server Scripts (names + events only) for conflicts.
- Output: a **proposal** document (stored as a Chikku File Job artefact) containing: plain-language explanation, native objects as JSON (Notification with condition expression, Assignment Rule, Workflow states/transitions), conflict report (e.g. existing "Verify Purchase Order" workflow), test plan.
- Native mechanisms safe to generate: **Notification** (condition is a restricted Python expression evaluated by `frappe.safe_eval`), **Assignment Rule**, **Workflow** definitions, **Auto Repeat**. Not generated: Server Scripts, Client Scripts, Webhooks.
- Apply step: "Create as disabled" (Action Request, admin confirm) → objects saved with `enabled=0`/`is_active=0` → admin enables in native UI. Workflows are exported unfiltered by `amba_garments` fixtures [V], so a newly created workflow would enter git on the next export. This needs a review step.
- Brief's stock-check example ("when a Sales Order is created, check stock; notify the manager if insufficient"): a Notification condition can only read fields of the document itself, so checking stock across warehouses cannot be expressed safely there [A — verify against `frappe.safe_eval` allow-list in the implementation phase]. The correct design is a reviewed `doc_events` hook written by a developer. Chikku should say so and produce a developer ticket (requirement + proposed code location), not generate code.

## 6. ERP learning and contextual guidance (Part 2.2 #11)

- `meta.explain_form`: field purpose (from DocField description/label), mandatory rules, current workflow state and who can act, typical next document (from ERPNext mappers).
- Curated "how-to" knowledge collection (R3) built from Amba SOPs.
- Guided tours (R3): generate a list of steps and highlight fields using `frappe.ui.form` APIs client-side; no navigation automation that saves data.

## 7. Proactive intelligence (R4)

- Opt-in subscriptions per user (e.g. "tell me each morning about overdue POs for my suppliers").
- Scheduler job per subscription runs **as that user** (`frappe.set_user`) with the same tools; delivers via Frappe `Notification Log` + panel badge; email only through native Notification/Email Queue.
- Budgets apply; subscriptions auto-pause after 3 consecutive failures; admin can disable globally.
