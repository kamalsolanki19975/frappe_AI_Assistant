// Renders the real Vue components (server-side, no browser needed) from given store states.
import assert from "node:assert/strict";
import { test } from "node:test";
import { createSSRApp, h } from "vue";
import { renderToString } from "@vue/server-renderer";
import App from "../../../public/js/chikku_ai/App.vue";
import { createStore } from "../../../public/js/chikku_ai/lib/store.js";

globalThis.window = globalThis;
globalThis.__ = (s, args) => (args ? s.replace(/\{(\d+)\}/g, (_, i) => args[i]) : s);

function storeWith(patch) {
	const store = createStore({ api: {}, storage: null, user: "u" });
	Object.assign(store.state, { open: true, loaded: true }, patch);
	return store;
}

async function render(store) {
	const app = createSSRApp({ render: () => h(App, { store, api: {}, avatarHtml: "<svg></svg>", getContext: () => null }) });
	return renderToString(app);
}

test("closed panel renders nothing", async () => {
	const html = await render(storeWith({ open: false }));
	assert.equal(html.replace(/<!--.*?-->/g, "").trim(), "");
});

test("open panel has header, status, language selector and accessible controls", async () => {
	const html = await render(storeWith({ assistant: { name: "Chikku AI" }, status: "ready", capabilities: { attachments: true, context: true } }));
	assert.match(html, /role="dialog"/);
	assert.match(html, /Chikku AI/);
	assert.match(html, /Online/);
	assert.match(html, /<select class="cai-lang"/);
	assert.match(html, /aria-label="Attach a file or image"/);
	// R2: no placeholder microphone; the mic only renders when voice input is configured (see r2.test.js)
	assert.doesNotMatch(html, /coming soon/);
	assert.match(html, /aria-label="Close"/);
	assert.match(html, /aria-live="polite"/);
});

test("greeting and not-configured notice on an empty conversation", async () => {
	const html = await render(storeWith({ greeting: "Hey Kamal! How's your day going?", status: "not_configured" }));
	assert.match(html, /Hey Kamal! How&#39;s your day going\?/);
	assert.match(html, /isn&#39;t connected to an AI provider/);
	assert.match(html, /Not configured/);
});

test("messages render: user text escaped, assistant sanitised HTML, typing indicator", async () => {
	const html = await render(storeWith({
		messages: [
			{ name: "u1", role: "user", content: "<b>hi</b>", status: "Completed" },
			{ name: "a1", role: "assistant", status: "Completed", content_html: "<p>Hello <strong>Kamal</strong></p>" },
			{ name: "a2", role: "assistant", status: "Processing", placeholder: true },
		],
	}));
	assert.match(html, /&lt;b&gt;hi&lt;\/b&gt;/); // user text is never rendered as HTML
	assert.match(html, /<strong>Kamal<\/strong>/);
	assert.match(html, /cai-typing/);
});

test("rich blocks: document cards, table and draft preview with confirm button", async () => {
	const html = await render(storeWith({
		messages: [{
			name: "a1", role: "assistant", status: "Completed", content_html: "<p>Done</p>",
			blocks: [
				{ type: "doc_cards", items: [{ doctype: "Sales Order", name: "SO-1", title: "J K FASHIONS" }] },
				{ type: "table", doctype: "Item", columns: ["name", "item_name"], rows: [["ITM-1", "Cotton"]] },
				{ type: "action_preview", action_request: "AR1", summary: "Draft SO", expiry_minutes: 10,
					preview: { title: "Draft Sales Order", fields: [{ label: "Customer", value: "J K FASHIONS" }],
						table: { columns: ["Item", "Qty"], rows: [["ITM-1", 2]] }, notes: ["Saved as Draft"] } },
			],
		}],
	}));
	assert.match(html, /href="\/desk\/sales-order\/SO-1"/);
	assert.match(html, /Cotton/);
	assert.match(html, /Draft Sales Order/);
	assert.match(html, /Create draft/);
	assert.match(html, /Expires in 10 minutes/);
});

test("draft result states", async () => {
	const block = { type: "action_preview", action_request: "AR1", summary: "s", expiry_minutes: 10, preview: { title: "T", fields: [] } };
	const done = await render(storeWith({ actions: { AR1: { state: "done", result: { doctype: "Sales Order", name: "SO-9" } } },
		messages: [{ name: "a", role: "assistant", status: "Completed", content_html: "", blocks: [block] }] }));
	assert.match(done, /Draft created:/);
	assert.match(done, /SO-9/);
	assert.doesNotMatch(done, /Create draft/);
	const err = await render(storeWith({ actions: { AR1: { state: "error", message: "Expired" } },
		messages: [{ name: "a", role: "assistant", status: "Completed", content_html: "", blocks: [block] }] }));
	assert.match(err, /Expired/);
});

test("error banner, mobile layout, expanded layout and history view", async () => {
	assert.match(await render(storeWith({ error: "Network problem" })), /role="alert"[\s\S]*Network problem/);
	assert.match(await render(storeWith({ layout: "mobile" })), /cai-panel--mobile/);
	assert.match(await render(storeWith({ expanded: true })), /cai-panel--expanded/);
	const hist = await render(storeWith({ view: "history", conversations: [{ name: "c1", title: "Stock check" }] }));
	assert.match(hist, /Recent conversations/);
	assert.match(hist, /Stock check/);
});

test("busy state shows the stop button instead of send", async () => {
	const html = await render(storeWith({ sending: true, activeReply: "r1", status: "busy" }));
	assert.match(html, /aria-label="Stop"/);
	assert.match(html, /Thinking/);
});
