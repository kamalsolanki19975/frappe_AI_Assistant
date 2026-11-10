// Panel behaviour: launcher open/close, greeting, sending, loading/error states, realtime events,
// language switching, conversation switching, attachments, draft confirmation, responsive layout.
import assert from "node:assert/strict";
import { test } from "node:test";
import { createStore } from "../../../public/js/chikku_ai/lib/store.js";

function memoryStorage() {
	const m = new Map();
	return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

function fakeApi(overrides = {}) {
	const calls = [];
	const rec = (name, value) => (...args) => {
		calls.push({ name, args });
		return typeof value === "function" ? value(...args) : Promise.resolve(value);
	};
	const api = {
		openSession: rec("openSession", {
			assistant: { name: "Chikku AI" }, provider_ready: true, language: "Auto",
			greeting: "Hey Kamal! How's your day going?", suggested_prompts: [], conversations: [],
			capabilities: { attachments: true, max_upload_mb: 1 },
		}),
		listConversations: rec("listConversations", []),
		getMessages: rec("getMessages", { messages: [{ name: "m1", role: "user", content: "old", seq: 1 }], has_more: false }),
		sendMessage: rec("sendMessage", (args) =>
			Promise.resolve({ conversation: args.conversation || "c1",
				message: { name: "u1", role: "user", content: args.text, status: "Queued", seq: 1 } })),
		cancelTurn: rec("cancelTurn", { ok: true }),
		setLanguage: rec("setLanguage", { ok: true }),
		upload: rec("upload", (file, conv) => Promise.resolve({ conversation: conv || "c9", file: "F1", file_name: file.name })),
		confirmAction: rec("confirmAction", { ok: true, doctype: "Sales Order", name: "SO-1" }),
		cancelAction: rec("cancelAction", { ok: true }),
		reissueAction: rec("reissueAction", { ok: true, token: "fresh-token" }),
		...overrides,
	};
	return { api, calls };
}

const make = (o = {}) => {
	const { api, calls } = fakeApi(o.api);
	const storage = o.storage || memoryStorage();
	const store = createStore({ api, storage, user: "kamal@x" });
	return { store, api, calls, storage };
};

test("launcher toggles the panel and loads the session only once", async () => {
	const { store, calls } = make();
	assert.equal(store.state.open, false);
	await store.togglePanel();
	assert.equal(store.state.open, true);
	await store.togglePanel();
	assert.equal(store.state.open, false);
	await store.openPanel();
	assert.equal(calls.filter((c) => c.name === "openSession").length, 1);
});

test("greeting shown once per tab session and not again after re-renders/re-opens", async () => {
	const storage = memoryStorage();
	const first = make({ storage });
	await first.store.openPanel();
	assert.equal(first.store.state.greeting, "Hey Kamal! How's your day going?");
	const second = make({ storage }); // e.g. page re-render with the same tab session
	await second.store.openPanel();
	assert.equal(second.store.state.greeting, null);
});

test("greeting never interrupts an active conversation", async () => {
	const { store } = make();
	store.state.messages.push({ name: "x", role: "user", content: "hi" });
	await store.openPanel();
	assert.equal(store.state.greeting, null);
});

test("not configured provider shows status and blocks sending", async () => {
	const { store, calls } = make({ api: { openSession: () => Promise.resolve({ provider_ready: false }) } });
	await store.openPanel();
	assert.equal(store.state.status, "not_configured");
	assert.equal(await store.send("hello"), false);
	assert.match(store.state.error, /not configured/);
	assert.equal(calls.filter((c) => c.name === "sendMessage").length, 0);
});

test("sending shows the message immediately, then a loading placeholder", async () => {
	const { store, calls } = make();
	await store.openPanel();
	const p = store.send("Why is this order pending?", { kind: "form", doctype: "Sales Order", docname: "SO-1" });
	assert.equal(store.state.messages[0].status, "Sending"); // optimistic
	assert.equal(store.state.sending, true);
	assert.equal(await p, true);
	const args = calls.find((c) => c.name === "sendMessage").args[0];
	assert.equal(args.text, "Why is this order pending?");
	assert.equal(args.context.docname, "SO-1");
	assert.ok(args.idempotency_key.length >= 16);
	assert.equal(store.state.status, "busy");
	assert.equal(store.state.messages.at(-1).placeholder, true);
	assert.equal(store.state.greeting, null);
});

test("context can be switched off by the user", async () => {
	const { store, calls } = make();
	await store.openPanel();
	store.state.contextEnabled = false;
	await store.send("hi", { kind: "form", doctype: "Sales Order", docname: "SO-1" });
	assert.equal(calls.find((c) => c.name === "sendMessage").args[0].context, null);
});

test("send failure shows an error state and keeps the text visible", async () => {
	const { store } = make({ api: { sendMessage: () => Promise.reject(new Error("You're sending messages too quickly.")) } });
	await store.openPanel();
	assert.equal(await store.send("hello"), false);
	assert.equal(store.state.error, "You're sending messages too quickly.");
	assert.equal(store.state.messages[0].status, "Failed");
	assert.equal(store.state.sending, false);
});

test("empty and double sends are ignored", async () => {
	const { store, calls } = make();
	await store.openPanel();
	assert.equal(await store.send("   "), false);
	store.state.sending = true;
	assert.equal(await store.send("x"), false);
	assert.equal(calls.filter((c) => c.name === "sendMessage").length, 0);
});

test("realtime events stream, show tool activity and finish the reply", async () => {
	const { store } = make();
	await store.openPanel();
	await store.send("hi");
	store.handleEvent({ type: "status", conversation: "c1", message: "r1" });
	store.handleEvent({ type: "delta", conversation: "c1", message: "r1", text: "Hello " });
	store.handleEvent({ type: "delta", conversation: "c1", message: "r1", text: "Kamal" });
	store.handleEvent({ type: "tool", conversation: "c1", message: "r1", tool: "search_items" });
	assert.equal(store.state.streaming.r1, "Hello Kamal");
	assert.equal(store.state.tools.r1, "search_items");
	store.handleEvent({ type: "delta", conversation: "OTHER", message: "zz", text: "leak" });
	assert.equal(store.state.streaming.zz, undefined); // other conversations ignored
	store.handleEvent({ type: "done", conversation: "c1", message: "r1",
		payload: { name: "r1", role: "assistant", status: "Completed", content_html: "<p>Hello Kamal</p>" } });
	assert.equal(store.state.status, "ready");
	assert.equal(store.state.sending, false);
	assert.equal(store.state.messages.at(-1).content_html, "<p>Hello Kamal</p>");
	assert.equal(store.state.streaming.r1, undefined);
});

test("error event replaces the placeholder", async () => {
	const { store } = make();
	await store.openPanel();
	await store.send("hi");
	store.handleEvent({ type: "error", conversation: "c1", message: "r1", text: "Something went wrong" });
	assert.equal(store.state.messages.at(-1).status, "Failed");
	assert.equal(store.state.sending, false);
});

test("stop button cancels the active reply", async () => {
	const { store, calls } = make();
	await store.openPanel();
	await store.send("hi");
	store.handleEvent({ type: "status", conversation: "c1", message: "r1" });
	await store.cancel();
	assert.deepEqual(calls.find((c) => c.name === "cancelTurn").args, ["r1"]);
});

test("language switching saves, rejects unknown values and reverts on failure", async () => {
	const ok = make();
	await ok.store.setLanguage("Hinglish");
	assert.equal(ok.store.state.language, "Hinglish");
	await ok.store.setLanguage("Klingon");
	assert.equal(ok.store.state.language, "Hinglish");
	const bad = make({ api: { setLanguage: () => Promise.reject(new Error("nope")) } });
	await bad.store.setLanguage("Hindi");
	assert.equal(bad.store.state.language, "Auto");
	assert.equal(bad.store.state.error, "nope");
});

test("conversation switching and new conversation", async () => {
	const { store } = make();
	await store.openPanel();
	store.state.view = "history";
	await store.selectConversation("c42");
	assert.equal(store.state.conversation, "c42");
	assert.equal(store.state.view, "chat");
	assert.equal(store.state.messages[0].content, "old");
	assert.equal(store.state.greeting, null);
	store.newConversation();
	assert.equal(store.state.conversation, null);
	assert.deepEqual(store.state.messages, []);
});

test("attachment control validates type and size before uploading", async () => {
	const { store, calls } = make();
	await store.openPanel();
	assert.equal(await store.attach({ name: "virus.exe", size: 10 }), false);
	assert.match(store.state.error, /not supported/);
	assert.equal(await store.attach({ name: "huge.pdf", size: 5 * 1024 * 1024 }), false);
	assert.match(store.state.error, /larger than/);
	assert.equal(await store.attach({ name: "invoice.pdf", size: 1000 }), true);
	assert.equal(store.state.pendingAttachments[0].file, "F1");
	assert.equal(store.state.conversation, "c9");
	store.removeAttachment("F1");
	assert.equal(store.state.pendingAttachments.length, 0);
	assert.equal(calls.filter((c) => c.name === "upload").length, 1);
});

test("draft confirmation uses the browser-only token once; reissues after reload", async () => {
	const { store, calls } = make();
	store.handleEvent({ type: "action_token", conversation: null, action_request: "AR1", token: "secret-token" });
	await store.confirmAction("AR1");
	assert.deepEqual(calls.find((c) => c.name === "confirmAction").args, ["AR1", "secret-token"]);
	assert.equal(store.state.actionTokens.AR1, undefined);
	assert.equal(store.state.actions.AR1.state, "done");
	await store.confirmAction("AR2"); // no token in memory (page reloaded)
	assert.ok(calls.find((c) => c.name === "reissueAction"));
	assert.deepEqual(calls.filter((c) => c.name === "confirmAction")[1].args, ["AR2", "fresh-token"]);
});

test("cancelling a draft forgets the token", async () => {
	const { store } = make();
	store.handleEvent({ type: "action_token", action_request: "AR3", token: "t" });
	await store.cancelAction("AR3");
	assert.equal(store.state.actionTokens.AR3, undefined);
	assert.equal(store.state.actions.AR3.state, "cancelled");
});

test("responsive layout and offline state", async () => {
	const { store } = make();
	store.setViewport(390);
	assert.equal(store.state.layout, "mobile");
	store.setViewport(1440);
	assert.equal(store.state.layout, "desktop");
	store.setOffline(true);
	assert.equal(store.state.status, "offline");
	store.setOffline(false);
	assert.equal(store.state.status, "ready");
});
