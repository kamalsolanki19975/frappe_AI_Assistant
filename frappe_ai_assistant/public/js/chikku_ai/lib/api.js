// Thin wrappers over Frappe's REST API. Uses the session cookie + CSRF token; no secrets here.
// fetch() is used instead of frappe.call so errors are shown inside the panel, not as Desk pop-ups.

const BASE = "frappe_ai_assistant.frappe_ai_assistant.api";

function stripHtml(s) {
	return String(s || "").replace(/<[^>]*>/g, "").trim();
}

export function extractError(body, status) {
	try {
		if (body && body._server_messages) {
			const msgs = JSON.parse(body._server_messages);
			if (msgs && msgs.length) {
				const first = typeof msgs[0] === "string" ? JSON.parse(msgs[0]) : msgs[0];
				const text = stripHtml(first.message || first);
				if (text) return new Error(text);
			}
		}
		if (body && body.exception) {
			const parts = String(body.exception).split(":");
			const text = stripHtml(parts.slice(1).join(":"));
			if (text) return new Error(text);
		}
	} catch (e) {
		/* fall through */
	}
	if (status === 429) return new Error("Too many requests. Please wait a moment.");
	if (status === 403) return new Error("You don't have access to this.");
	if (!status) return new Error("Network problem. Please check your connection.");
	return new Error("");
}

async function request(method, args = {}, type = "POST", { body: rawBody } = {}) {
	let url = `/api/method/${BASE}.${method}`;
	const headers = { Accept: "application/json", "X-Frappe-CSRF-Token": window.frappe?.csrf_token || "" };
	const init = { method: type, headers, credentials: "same-origin" };
	if (rawBody) {
		init.body = rawBody;
	} else if (type === "GET") {
		const qs = new URLSearchParams();
		for (const [k, v] of Object.entries(args)) if (v !== undefined && v !== null) qs.append(k, v);
		const s = qs.toString();
		if (s) url += `?${s}`;
	} else {
		headers["Content-Type"] = "application/json";
		init.body = JSON.stringify(args);
	}
	let res;
	try {
		res = await fetch(url, init);
	} catch (e) {
		throw extractError(null, 0);
	}
	let body = {};
	try {
		body = await res.json();
	} catch (e) {
		body = {};
	}
	if (!res.ok || body.exc_type) throw extractError(body, res.status);
	return body.message;
}

export const api = {
	openSession: (conversation) => request("chat.open_session", conversation ? { conversation } : {}),
	listConversations: () => request("chat.list_conversations", {}, "GET"),
	getMessages: (conversation, before_seq) =>
		request("chat.get_messages", before_seq ? { conversation, before_seq } : { conversation }, "GET"),
	sendMessage: (args) =>
		request("chat.send_message", {
			...args,
			context: args.context ? JSON.stringify(args.context) : null,
			attachments: JSON.stringify(args.attachments || []),
		}),
	cancelTurn: (message) => request("chat.cancel_turn", { message }),
	setLanguage: (language) => request("chat.set_language", { language }),
	setFeedback: (message, feedback) => request("chat.set_feedback", { message, feedback }),
	archiveConversation: (conversation) => request("chat.archive_conversation", { conversation }),
	confirmAction: (action_request, token) => request("actions.confirm", { action_request, token }),
	cancelAction: (action_request) => request("actions.cancel", { action_request }),
	reissueAction: (action_request) => request("actions.reissue", { action_request }),
	importUpdateMapping: (session, p) => request("imports.update_mapping", { session, mapping: JSON.stringify(p.mapping),
		defaults: JSON.stringify(p.defaults || {}), import_type: p.import_type, parent_key_column: p.parent_key_column }),
	importValidate: (session) => request("imports.validate", { session }),
	importBackToMapping: (session) => request("imports.back_to_mapping", { session }),
	importRequestConfirmation: (session) => request("imports.request_confirmation", { session }),
	importRetry: (session) => request("imports.retry_failed", { session }),
	importCancel: (session) => request("imports.cancel", { session }),
	importSaveTemplate: (session, title) => request("imports.save_template", { session, title }),
	workflowRequestApply: (proposal) => request("workflows.request_apply", { proposal }),
	voiceConsent: () => request("voice.give_consent", {}),
	speak: (message) => request("voice.speak", { message }),
	transcribe(blob) {
		const form = new FormData();
		form.append("audio", blob, "voice.webm");
		return request("voice.transcribe", {}, "POST", { body: form });
	},
	upload(file, conversation) {
		const form = new FormData();
		form.append("file", file, file.name);
		if (conversation) form.append("conversation", conversation);
		return request("files.upload", {}, "POST", { body: form });
	},
};
