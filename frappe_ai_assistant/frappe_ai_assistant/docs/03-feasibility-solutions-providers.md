# Deliverables 3, 4, 5 — Feasibility, Existing Solutions, Provider Architecture

Legend: **[V]** verified on this server/source · **[D]** from official docs/vendor pages · **[A]** assumption / needs proof of concept.

---

# Part A — Technical Feasibility Report (Deliverable 3)

Categories: **VF** verified feasible · **FI** feasible with additional integration (external API) · **EI** requires external infrastructure · **POC** requires proof of concept · **NR** not recommended · **NF** not feasible under current constraints.

| Capability | Class | Evidence / reasoning |
|---|---|---|
| Floating global chatbot in Desk | **VF** | `app_include_js` + esbuild bundles; Vue 3 ships with frappe; `saarthi_home` already uses this pattern [V] |
| Single assistant settings (Single DocType) | **VF** | standard Frappe |
| Secure credential storage | **VF** | Password fields → `__Auth`, `get_decrypted_password` [V] `frappe/utils/password.py` |
| Personal greeting | **VF** | server-side `User.first_name` |
| Text chat with streaming | **FI** | provider API + `publish_realtime` to user room [V] |
| Permission-aware read tools | **VF** | `frappe.get_list`, `check_permission`, `apply_fieldlevel_read_permissions` [V] |
| Context-aware ERP intelligence | **VF** | router/form events client-side; server revalidation |
| Draft-first writes with confirmation tokens | **VF** | standard `insert()` as user; token DocType + Redis lock |
| Multi-company protection | **VF (config dependency)** | depends on User Permissions, **of which none exist today** [V]. Chikku can scope by user default company but cannot invent restrictions. |
| English/Hindi/Hinglish conversation | **FI + POC** | frontier LLMs handle Hindi; Hinglish quality must be measured per model [A] |
| Image understanding (screenshots, photos) | **FI** | vision-capable LLM; Pillow pre-processing [V] |
| PDF/DOCX text extraction | **VF** | pdfplumber, pypdf, lxml installed [V] |
| Scanned PDF / OCR | **FI + POC** | pypdfium2 rasterise [V] + vision LLM or OCR API. Tesseract poor on Hindi per public reports [D]; self-hosted PaddleOCR **NF** on 1 vCPU/1.9 GB |
| Invoice field extraction with confidence | **FI + POC** | LLM structured output + deterministic validators (GSTIN checksum, arithmetic, tax). Accuracy must be benchmarked on 50+ real Amba invoices. |
| Excel analyse | **VF** | openpyxl read-only [V] |
| Excel generate (charts, CF, validation, freeze panes) | **VF** | XlsxWriter 3.2.9 [V] |
| Excel modify preserving original | **VF with caveat** | openpyxl drops charts/images/pivots on save, so modification = "rebuild via XlsxWriter" or openpyxl only when no unsupported parts (Deliverable 10) |
| Conversational import into eligible DocTypes | **VF** | native Data Import + `template_options` + `data_import_providers` hook [V] |
| Parent-child import | **VF** | native single-sheet format; Chikku transforms multi-sheet → native format |
| Multi-sheet joins | **VF** | done in Chikku transform step (pure Python) |
| All-or-nothing import | **NR** | native importer commits per record [V]; emulating global rollback would require a competing importer. Use dry-run + idempotent retry instead. |
| BI on predefined metrics | **VF** | permission-aware aggregation via `frappe.get_list` with `group_by`/aggregate fields or running permitted reports |
| BI with arbitrary LLM-generated SQL | **NR** | violates security model |
| Running existing Query/Script Reports | **VF** | `frappe.desk.query_report.run` as user; `is_permitted()` [V] |
| Anomaly detection | **POC** | simple statistical rules (z-score, IQR) first; no ML infra |
| Knowledge assistant: keyword search | **VF** | MariaDB InnoDB FULLTEXT on a Chikku chunk table |
| Knowledge assistant: semantic search | **FI** | external embeddings API + in-Python cosine over ≤ ~20k chunks (POC for latency on 1 vCPU) |
| Native vector search in DB | **NF now / EI later** | MariaDB 10.11 has no VECTOR type; 11.8 LTS does [D]. Upgrade is an infrastructure decision. |
| Dedicated vector DB (Qdrant, pgvector) | **EI / NR for R3** | extra service on a memory-starved host; revisit if corpus > 50k chunks |
| Voice push-to-talk | **FI** | MediaRecorder + STT API (Sarvam / OpenAI / Google) |
| Spoken replies | **FI** | TTS API, or browser `speechSynthesis` fallback (quality varies) |
| Continuous voice conversation | **EI + POC** | needs a long-lived WebSocket relay; gunicorn is a poor fit. Requires a small separate async service (or provider-direct ephemeral tokens). R4. |
| Self-hosted STT/TTS/LLM | **NF** | hardware |
| Workflow proposals (Notification, Assignment Rule, Workflow JSON) | **VF** | generated as JSON; saved disabled after admin confirmation |
| AI-generated Server Scripts | **NR** | arbitrary code execution; `server_script_enabled=1` on this bench makes this especially risky |
| Proactive notifications | **VF (R4)** | scheduler + `Notification Log` / realtime; must run as each target user |
| Activity Centre / admin dashboards | **VF** | DocTypes + Number Cards/Dashboard Charts |
| Native Frappe MCP reuse | **NF now** | not in installed 16.52.0 [V]; when available it exposes delete + document methods (submit/cancel) [D]; **NR** for Chikku's internal registry |
| Mobile (Desk responsive) | **VF** | panel becomes full-screen sheet; subject to existing Access Control mobile restriction |

---

# Part B — Existing AI Solution Comparison (Deliverable 4)

| Project | Licence | Frappe v16 | Architecture | Write policy | Data egress | Maintenance signal | Verdict |
|---|---|---|---|---|---|---|---|
| **Jarvis** (aerele/jarvis), *on this bench, not installed* | **AGPL-3.0-only** [V `hooks.py`, `license.txt`] | yes, `version-16` branch, v16.5.1 [V] | Bench app + **hosted agent runtime** (`wss://<slug>.jarvis.aerele.in`, control plane `fleet.klerk.in`) [V `agent_ws.py`, `hooks.py`]; ~117 tools incl. `cancel_doc`, `delete_doc`, `amend_doc`, `apply_workflow_action` [V `jarvis/tools/`]; File Box, Approval Board, Skills, Macros, Triggers, Wiki | Configurable; admins may allow reversible changes without confirmation; destructive tools exist but are gated | Conversation/tool traffic flows through vendor-hosted runtime; billing/usage telemetry pushed (`usage_push`, `error_push`) [V] | Very active, strict review culture (`CLAUDE.md`) | **Do not install alongside Chikku. Do not copy code** (AGPL into MIT app would force AGPL obligations). Valuable **design reference**: role gate (`permissions.py`), company scope guard (`tools/_company_scope.py`), write-risk classifier (`_write_risk.py`), import preview sandbox (`_import_preview.py`), file-scan worker isolation (`_file_scan_process.py`), xlsx charts. |
| **Frappe Assistant Core** (buildswithpaul) | **AGPL-3.0** [D GitHub] | v15/v16 [D] | MCP server (OAuth 2 + PKCE) for external clients; optional FAC Chat (requires paid FAC Cloud, forwards payloads to it) [D] | `create/update/delete_document`, `document_action`, `run_workflow`; **`run_python_code`, `run_database_query`** [D] | MCP mode: to whichever client the user connects | 922 commits, 334★, 2 open security alerts [D] | **Not suitable** as Chikku core: code/SQL tools conflict with policy; AGPL. Could be considered *separately* for power users using Claude Desktop, with tools restricted, but that is outside Chikku. |
| **Frappe built-in MCP** (`/api/v2/mcp`) | MIT (Frappe) | **not in installed 16.52.0** [V]; documented 2026-10 [D] | 13 tools; OAuth or API token; per-call REST v2 permission checks; **Enable MCP** in System Settings → AI Agents, *on by default for new sites* [D] | `create/update/delete_document`; `call_document_method` can reach `submit`/`cancel` [D] | to external MCP client | official | **Exclude from Chikku's internal registry.** When an upgrade brings it, the admin must explicitly decide whether to enable it (it bypasses draft-first for any API-token holder). Possible future: expose *Chikku's* read-only registry as an MCP server. |
| **Raven AI agents** (now `frappe/raven`) | **AGPL-3.0** [D] | v15 ≥15.111 and v16 [D] | Messaging app; agents on OpenAI Assistants API or "compatible provider"; functions call standard Frappe APIs with user permission checks [D] | function-defined; can create docs | OpenAI | active (≈4.7k commits) [D] | Not a fit: messaging product, AGPL, Assistants-API coupling. Its function-definition UI is a useful idea for admin-defined read tools (R3+). |
| **ERPNext Copilot** (ameenmari/erpnext_copilot) | **MIT** [D] | unspecified [D] | Desk page; LLM emits JSON query plan validated before execution; reads via `get_list`/`get_doc`; OpenAI-compatible (OpenAI, Azure, Groq, Ollama) [D] | draft-only create with Approve; no delete/cancel/submit [D] | question + field metadata only [D] | MVP, 5 commits, 13★ [D] | Philosophically closest and MIT-licensed, but too immature to depend on. **Code may be read and patterns adapted with attribution** (MIT-compatible). |
| ERPNext AI Copilot (ecosire.com) | commercial | v15/v16 claimed | build-to-order service | — | — | — | Not an open-source candidate. |
| Generic RAG stacks (LlamaIndex, LangChain, Haystack) | MIT/Apache | n/a | large dependency trees | — | — | — | **Not recommended** inside the bench process: dependency conflicts with frappe's pins (pydantic, requests), memory, upgrade drag. A ~300-line in-house chunk/embed/retrieve service is enough for R3. |
| LiteLLM (gateway / SDK) | MIT (core) | n/a | unified API over 100+ providers | — | — | widely used; **PyPI supply-chain compromise 2026-03-24, versions 1.82.7/1.82.8 credential-stealing** [D] | **Not as an in-process dependency.** Optional later as a *separately hosted, pinned, hash-verified* proxy if many providers are needed. |

**Conclusion:** no existing project satisfies all fixed decisions (inside `amba_garments`, MIT-compatible, draft-first, no external control plane, provider-agnostic). **Build in-house**, borrowing patterns (not code) from Jarvis and reading ERPNext Copilot for ideas.

---

# Part C — Provider Architecture Report (Deliverable 5)

## C1. Requirements that drive provider choice

1. Reliable **tool calling** with parallel calls and JSON-schema arguments.
2. **Structured outputs** (strict JSON schema) for extraction and mapping.
3. **Vision** (images and rasterised PDF pages).
4. **Hindi + Hinglish** quality (measured, not assumed).
5. **Streaming**.
6. **Prompt-injection robustness** (documents are untrusted).
7. Cost per conversation at ~5 active users (current site) up to ~50.
8. **Privacy and data residency**: business data (GSTINs, prices, party names) leaves the server. Contractual zero-retention / no-training terms are required; India-region processing is preferred where available.

## C2. Candidate matrix (capabilities as documented; quality to be measured)

| Provider | Tool calling | Structured output | Vision | Hindi / Hinglish | Streaming | Residency notes | Role |
|---|---|---|---|---|---|---|---|
| Anthropic (Claude, e.g. Sonnet / Haiku 5.5 tiers) | strong | JSON-schema tools | yes (images, PDFs) | good Hindi; Hinglish **measure** | SSE | US/global; enterprise zero-retention terms available [D-verify] | primary reasoning candidate |
| OpenAI (GPT family) | strong | strict JSON schema | yes | good; Hinglish **measure** | SSE | data-residency programmes exist for some regions [D-verify] | primary candidate; also STT/TTS/embeddings |
| Google Gemini | strong | JSON schema | yes, long context | good; strong Indic coverage [A] | SSE | regional endpoints via Vertex AI (incl. India regions) [D-verify] | primary candidate; also embeddings |
| OpenAI-compatible hosts (Groq, Together, Azure OpenAI, vLLM, Ollama) | varies by model | varies | varies | varies | yes | Azure OpenAI has India regions [D-verify] | secondary / fallback / cheap tier |
| Sarvam AI | LLM available; tool-calling maturity **POC** | — | Sarvam Vision [D] | built for Indic incl. code-mixed [D] | yes | **India-hosted** [D] | **STT (`saaras:v3`), TTS (Bulbul v3/v4)** [D] |
| Self-hosted (Ollama/vLLM) | model-dependent | | | | | on-prem | **NF on current hardware** |

Prices change monthly; record them in `Chikku AI Model` rows (input/output price per 1M tokens) so cost estimates stay configurable instead of hardcoded.

## C3. Specialised model slots

Configuration exposes **slots**, each bound to a provider+model:

| Slot | Default recommendation (subject to bake-off) | Fallback |
|---|---|---|
| `reasoning` (chat + tools) | the winner of the bake-off among Anthropic / OpenAI / Gemini mid-tier models | second-best of the same bake-off |
| `fast` (classification, language detect, titles, mapping suggestions) | small/cheap tier of the same provider | heuristic code path |
| `vision_extract` (documents, images) | same as `reasoning` if vision-capable | second provider |
| `stt` | Sarvam `saaras:v3` (Hindi/Hinglish/Indian accents) | OpenAI or Google STT |
| `tts` | Sarvam Bulbul | browser `speechSynthesis` |
| `embedding` | a multilingual embedding model (OpenAI / Gemini / Cohere multilingual) chosen on a Hindi retrieval test | keyword-only (FULLTEXT) |

## C4. Adapter architecture

```
chikku_ai/providers/
  base.py          ProviderAdapter(ABC): chat(messages, tools, schema, stream) -> Iterator[Event]
                   embed(texts) ; transcribe(audio) ; synthesize(text)
                   capabilities: {tools, vision, json_schema, stream, max_context}
  types.py         neutral types: Message, ContentPart(text|image|file), ToolSpec, ToolCall,
                   Usage(input_tokens, output_tokens, cached_tokens), Event(delta|tool_call|done|error)
  openai_compat.py one adapter for OpenAI + any OpenAI-compatible endpoint (base_url configurable)
  anthropic.py     Messages API
  gemini.py        Gemini API (or via OpenAI-compatible endpoint if feature-complete)
  sarvam.py        STT/TTS only
  router.py        slot → (adapter, model); retries; fallback; circuit breaker (Redis)
  errors.py        ProviderTimeout, RateLimited, ContextTooLong, ContentFiltered, ProviderDown
```

**Implementation choice: thin adapters over `requests` (already installed), not vendor SDKs, not LiteLLM.**

| Option | Pros | Cons | Decision |
|---|---|---|---|
| Thin adapters on `requests` | no new deps; full control of timeouts/streaming; no pin conflicts with frappe (pydantic 2.12, requests 2.33) | ~300 lines per provider; must track API changes | **Chosen** |
| Official SDKs (`openai`, `anthropic`, `google-genai`) | maintained by vendors; typed | add `httpx` + transitive deps; three SDKs to keep compatible with frappe pins; version drift on bench updates | acceptable alternative if team prefers; isolate behind the same interface |
| LiteLLM in-process | 100+ providers | very large dependency surface; March 2026 compromise | rejected in-process |
| LiteLLM / gateway as separate service | central keys, budgets | extra infra on starved host | possible R4+ |

ERP business services and tools **never import an adapter**. They return neutral data; only the orchestrator talks to `router.py`. Switching provider = changing settings rows.

## C5. Reliability policy

- Timeouts: connect 10 s, first-byte 30 s, total 120 s (turn), streaming idle 30 s.
- Retries: 2 retries with exponential backoff + jitter on 429/5xx/timeouts *before any tool executed in the turn*; after a tool executed, retry the model call only (tools are idempotent reads; drafts are token-gated so never re-run).
- Circuit breaker: 5 failures/60 s → slot marked down for 120 s → use fallback slot; UI shows "using backup model".
- Context window management: trim history by token estimate (chars/3.5 heuristic, no tiktoken dependency), keep system prompt + last N turns + conversation summary (generated by `fast` slot when history > 60 % of window).
- Budget guard: pre-check user/site budget before calling; record usage after.

## C6. Bake-off protocol (before final provider selection)

1. 450 prompts: 150 English, 150 Devanagari Hindi, 150 Hinglish; categories: lookup, comparison, draft preparation, ambiguous names, date phrases, injection attempts embedded in documents, extraction from 30 real invoices.
2. Metrics: tool-selection accuracy, argument accuracy, numeric faithfulness (0 tolerance), language match, injection resistance, p50/p90 latency, cost per 100 turns.
3. Run with the real tool registry against a **copy** of `ambatest.local`.
4. Choose `reasoning` and `fast` slots from results. Document in an ADR.
