// R2 panel behaviour and rendering: import studio, voice, speech, alerts, workflow approval, new blocks.
import assert from "node:assert/strict";
import { test } from "node:test";
import { createSSRApp, h } from "vue";
import { renderToString } from "@vue/server-renderer";
import App from "../../../public/js/chikku_ai/App.vue";
import BlockView from "../../../public/js/chikku_ai/components/BlockView.vue";
import { createStore } from "../../../public/js/chikku_ai/lib/store.js";

globalThis.window = globalThis;
globalThis.__ = (s, args) => (args ? s.replace(/\{(\d+)\}/g, (_, i) => args[i]) : s);

function mkStore(api = {}) {
	const calls = [];
	const wrap = Object.fromEntries(Object.entries(api).map(([k, fn]) => [k, (...a) => { calls.push([k, a]); return fn(...a); }]));
	const store = createStore({ api: wrap, storage: null, user: "u" });
	return { store, calls };
}

const block = (over = {}) => ({ type: "import_mapping", session: "S1", doctype: "Customer", status: "Mapping", import_type: "Insert New Records",
	modes: ["Insert New Records"], rows: 5, columns: [{ index: 0, header: "Customer Name", field: "customer_name", confidence: 1, samples: ["A"] },
		{ index: 1, header: "Notes", field: null, confidence: 0, samples: ["x"] }],
	fields: [{ value: "customer_name", label: "Customer Name", reqd: 1 }, { value: "customer_group", label: "Customer Group", reqd: 1 }],
	defaults: {}, missing_mandatory: [{ value: "customer_group", label: "Customer Group" }], ...over });

async function render(component, props) {
	return renderToString(createSSRApp({ render: () => h(component, props) }));
}

// ---------------------------------------------------------------- store
test("import: validate saves mapping then waits for the background result", async () => {
	const { store, calls } = mkStore({
		importUpdateMapping: async () => block(),
		importValidate: async () => ({ queued: true }),
	});
	await store.importAction("validate", "S1", { mapping: [], defaults: {} });
	assert.deepEqual(calls.map((c) => c[0]), ["importUpdateMapping", "importValidate"]);
	assert.equal(store.state.imports.S1.status, "Validating");
	store.handleEvent({ type: "import_status", session: "S1", status: "Validated", preview: { type: "import_preview", session: "S1", valid_records: 3 } });
	assert.equal(store.state.imports.S1.block.valid_records, 3);
	assert.equal(store.state.imports.S1.busy, false);
});

test("import: confirm uses a browser-only token and progress/completion events update the card", async () => {
	const { store, calls } = mkStore({
		importRequestConfirmation: async () => ({ action_request: "AR9", token: "tok" }),
		confirmAction: async () => ({ ok: true }),
	});
	await store.importAction("confirm", "S1");
	assert.deepEqual(calls[1], ["confirmAction", ["AR9", "tok"]]);
	assert.equal(store.state.imports.S1.status, "Confirmed");
	store.handleEvent({ type: "import_status", session: "S1", status: "Importing", data_import: "DI-1" });
	store.handleEvent({ type: "import_progress", data_import: "DI-1", current: 2, total: 4 });
	assert.equal(store.state.imports.S1.current, 2);
	store.handleEvent({ type: "import_status", session: "S1", status: "Partially Completed", success: 3, failed: 1, error_file: "/private/files/e.xlsx" });
	assert.equal(store.state.imports.S1.failed, 1);
});

test("import: validation error returns to mapping with the message", async () => {
	const { store } = mkStore({});
	store.handleEvent({ type: "import_status", session: "S2", status: "Mapping", error: "Sheet not found." });
	assert.equal(store.state.imports.S2.message, "Sheet not found.");
	assert.equal(store.state.imports.S2.status, "Mapping");
});

test("import: retry adds a new mapping card with only failed rows", async () => {
	const { store } = mkStore({ importRetry: async () => ({ session: "S3", rows_to_retry: 2, mapping_block: block({ session: "S3" }) }) });
	await store.importAction("retry", "S1");
	assert.equal(store.state.imports.S3.status, "Mapping");
	assert.equal(store.state.messages.at(-1).blocks[0].session, "S3");
});

test("voice: transcript fills the composer flow; errors are shown; stale results are ignored", async () => {
	const { store } = mkStore({ transcribe: async () => ({ request: "R1" }) });
	await store.sendAudio(new Blob(["x"]));
	assert.equal(store.state.voice.status, "transcribing");
	store.handleEvent({ type: "transcript", request: "OLD", text: "ignore me" });
	assert.equal(store.state.voice.transcript, null);
	store.handleEvent({ type: "transcript", request: "R1", text: "aaj ki sales dikhao" });
	assert.equal(store.state.voice.transcript, "aaj ki sales dikhao");
	assert.equal(store.state.voice.status, "idle");
	const fail = mkStore({ transcribe: async () => { throw new Error("Voice input is not configured."); } });
	await fail.store.sendAudio(new Blob(["x"]));
	assert.equal(fail.store.state.voice.error, "Voice input is not configured.");
});

test("speech and alerts events", async () => {
	const { store } = mkStore({ speak: async () => ({ request: "Q" }) });
	await store.speak("M1");
	assert.equal(store.state.speech.loading, "M1");
	store.handleEvent({ type: "speech", message: "M1", audio: "data:audio/wav;base64,AAA" });
	assert.equal(store.state.speech.audio, "data:audio/wav;base64,AAA");
	store.handleEvent({ type: "alert", alert: "A1", title: "2 overdue" });
	assert.equal(store.state.capabilities.new_alerts, 1);
});

test("workflow approval goes through request_apply + confirm", async () => {
	const { store, calls } = mkStore({ workflowRequestApply: async () => ({ action_request: "AR", token: "T" }),
		confirmAction: async () => ({ ok: true, name: "WF-1" }) });
	await store.applyProposal("P1");
	assert.equal(store.state.proposals.P1.state, "done");
	assert.deepEqual(calls.map((c) => c[0]), ["workflowRequestApply", "confirmAction"]);
});

// ---------------------------------------------------------------- rendering
test("mic button only appears when voice input is really available", async () => {
	const on = createStore({ api: {}, storage: null, user: "u" });
	Object.assign(on.state, { open: true, loaded: true });
	on.state.capabilities = { ...on.state.capabilities, voice_input: true };
	const off = createStore({ api: {}, storage: null, user: "u" });
	Object.assign(off.state, { open: true, loaded: true });
	const htmlOn = await render(App, { store: on, api: {}, avatarHtml: "", getContext: () => null });
	const htmlOff = await render(App, { store: off, api: {}, avatarHtml: "", getContext: () => null });
	assert.match(htmlOn, /aria-label="Speak"/);
	assert.doesNotMatch(htmlOff, /aria-label="Speak"/);
	assert.doesNotMatch(htmlOff, /coming soon/);
	assert.match(htmlOn, /href="\/desk\/chikku-activity"/);
});

test("KPI, chart source note, file download and citations blocks", async () => {
	const s = createStore({ api: {}, storage: null, user: "u" });
	const kp = await render(BlockView, { store: s, block: { type: "kpis", items: [{ label: "Net Sales", value: 1234.5, format: "number" }] } });
	assert.match(kp, /Net Sales/);
	const src = await render(BlockView, { store: s, block: { type: "source", trace: { method: "Sum of base net total", company: "Amba", source_doctypes: ["Sales Invoice"] } } });
	assert.match(src, /How this was calculated/);
	assert.match(src, /Sum of base net total/);
	const file = await render(BlockView, { store: s, block: { type: "file", file_name: "Sales.xlsx", url: "/private/files/Sales.xlsx", size_kb: 12, detail: "2 sheets" } });
	assert.match(file, /href="\/private\/files\/Sales.xlsx"/);
	const evil = await render(BlockView, { store: s, block: { type: "file", file_name: "x", url: "javascript:alert(1)", size_kb: 1 } });
	assert.doesNotMatch(evil, /javascript:/);
	const cit = await render(BlockView, { store: s, block: { type: "citations", items: [{ n: 1, document: "KD1", title: "Leave Policy", heading: "Sick leave", page: 2 }] } });
	assert.match(cit, /\[1\]/);
	assert.match(cit, /Leave Policy/);
});

test("extraction block shows evidence, confidence and issues without inventing values", async () => {
	const s = createStore({ api: {}, storage: null, user: "u" });
	const html = await render(BlockView, { store: s, block: { type: "extraction", file: "inv.pdf", method: "pattern",
		fields: { grand_total: { value: 1180, confidence: "pattern", evidence: "Grand Total 1180.00" } }, items: [],
		matches: { supplier: "SUP-1" }, issues: [{ level: "warning", message: "Due date was not found in the document." }],
		low_confidence: [], note: "No AI model is configured" } });
	assert.match(html, /Grand Total 1180.00/);
	assert.match(html, /SUP-1/);
	assert.match(html, /Due date was not found/);
	assert.match(html, /No AI model is configured/);
});

test("import studio renders mapping, missing mandatory fields, then preview", async () => {
	const s = createStore({ api: {}, storage: null, user: "u" });
	const map = await render(BlockView, { store: s, block: block() });
	assert.match(map, /Customer Name/);
	assert.match(map, /don&#39;t import/);
	assert.match(map, /Required fields not mapped yet:.*Customer Group/s);
	assert.match(map, /Validate/);
	s.state.imports.S1 = { status: "Validated", block: { type: "import_preview", session: "S1", doctype: "Customer", status: "Validated",
		total_rows: 5, valid_records: 1, invalid_rows: 2, duplicate_rows: 2, errors: [{ row: 3, field: "Customer Group", message: "not found" }],
		warnings: ["Native import commits record by record"], estimated_seconds: 5, sample: [] } };
	const prev = await render(BlockView, { store: s, block: block() });
	assert.match(prev, /Import 1 record\(s\)/);
	assert.match(prev, /not found/);
	assert.match(prev, /nothing is dropped silently/);
	s.state.imports.S1 = { status: "Completed", success: 1, failed: 0 };
	assert.match(await render(BlockView, { store: s, block: block() }), /1 imported, 0 failed/);
});

test("workflow proposal shows transitions, issues and the inactive-only apply button", async () => {
	const s = createStore({ api: {}, storage: null, user: "u" });
	const html = await render(BlockView, { store: s, block: { type: "workflow_proposal", proposal: "P1", kind: "Workflow", title: "PO Approval",
		doctype: "Purchase Order", can_apply: true, issues: [{ level: "warning", message: "already has an active workflow" }],
		spec: { transitions: [{ state: "Draft", action: "Approve", next_state: "Approved", allowed: "Purchase Manager", condition: "doc.grand_total > 100000" }] } } });
	assert.match(html, /Create \(inactive\)/);
	assert.match(html, /Nothing is activated automatically/);
	assert.match(html, /doc.grand_total &gt; 100000/);
	assert.match(html, /already has an active workflow/);
});
