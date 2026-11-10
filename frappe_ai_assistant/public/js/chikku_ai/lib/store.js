// Chikku AI panel state and behaviour. UI components only read state and call actions.
// Dependencies (api, storage, realtime, user) are injected so this module is unit-testable.

import { reactive } from "vue";

export const ALLOWED_EXTENSIONS = ["pdf", "png", "jpg", "jpeg", "webp", "xlsx", "csv", "docx", "txt"];
export const LANGUAGES = ["Auto", "English", "Hindi", "Hinglish"];
const MOBILE_BREAKPOINT = 640;

let uid = 0;
const tempId = () => `tmp-${Date.now()}-${++uid}`;
const newKey = () =>
	(globalThis.crypto && globalThis.crypto.randomUUID && globalThis.crypto.randomUUID().replace(/-/g, "")) ||
	`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;

export function createStore({ api, storage, user, translate = (s) => s }) {
	const __ = translate;
	const state = reactive({
		open: false,
		expanded: false,
		layout: "desktop",
		view: "chat", // chat | history
		loaded: false,
		loading: false,
		status: "ready", // ready | busy | not_configured | offline | error
		assistant: { name: "Chikku AI", avatar: null, description: "" },
		capabilities: { attachments: false, voice: false, context: true, drafts: false, max_upload_mb: 10 },
		language: "Auto",
		suggested: [],
		conversations: [],
		conversation: null,
		messages: [],
		hasMore: false,
		greeting: null,
		pendingAttachments: [],
		uploading: false,
		error: null,
		sending: false,
		activeReply: null,
		streaming: {}, // reply name -> streamed text
		tools: {}, // reply name -> last tool name
		actionTokens: {}, // action request -> token (memory only, never persisted)
		actions: {}, // action request -> {state, result, message}
		contextEnabled: true,
		imports: {}, // import session -> {status, block, busy, message, success, failed, error_file, data_import, current, total}
		proposals: {}, // workflow proposal -> {state, result, message}
		voice: { status: "idle", request: null, transcript: null, error: null },
		speech: { message: null, audio: null, loading: null, error: null },
	});

	const greetedKey = `chikku_ai:greeted:${user}`;

	function setViewport(width) {
		state.layout = width < MOBILE_BREAKPOINT ? "mobile" : "desktop";
	}

	async function init() {
		if (state.loaded || state.loading) return;
		state.loading = true;
		state.error = null;
		try {
			const r = await api.openSession();
			state.assistant = r.assistant || state.assistant;
			state.capabilities = { ...state.capabilities, ...(r.capabilities || {}) };
			state.language = r.language || "Auto";
			state.suggested = r.suggested_prompts || [];
			state.conversations = r.conversations || [];
			state.status = r.provider_ready ? "ready" : "not_configured";
			// Greet once per browser tab session, only on an empty conversation, never mid-conversation.
			if (r.greeting && !state.messages.length && !safeGet(greetedKey)) {
				state.greeting = r.greeting;
				safeSet(greetedKey, "1");
			}
			state.loaded = true;
		} catch (e) {
			state.error = errorText(e, __("Chikku AI could not start. Please try again."));
			state.status = "error";
		} finally {
			state.loading = false;
		}
	}

	function safeGet(k) {
		try {
			return storage && storage.getItem(k);
		} catch (e) {
			return null;
		}
	}
	function safeSet(k, v) {
		try {
			storage && storage.setItem(k, v);
		} catch (e) {
			/* storage unavailable: greeting may repeat, nothing else breaks */
		}
	}

	async function openPanel() {
		state.open = true;
		await init();
	}
	function closePanel() {
		state.open = false;
	}
	function togglePanel() {
		return state.open ? closePanel() : openPanel();
	}
	function toggleExpand() {
		state.expanded = !state.expanded;
	}

	function newConversation() {
		state.conversation = null;
		state.messages = [];
		state.pendingAttachments = [];
		state.hasMore = false;
		state.view = "chat";
		state.error = null;
		state.activeReply = null;
		state.sending = false;
		if (state.status === "busy") state.status = "ready";
	}

	async function refreshConversations() {
		try {
			state.conversations = (await api.listConversations()) || [];
		} catch (e) {
			/* non-critical */
		}
	}

	async function selectConversation(name) {
		if (!name) return;
		state.error = null;
		try {
			const r = await api.getMessages(name);
			state.conversation = name;
			state.messages = r.messages || [];
			state.hasMore = !!r.has_more;
			state.greeting = null;
			state.view = "chat";
			state.pendingAttachments = [];
		} catch (e) {
			state.error = errorText(e, __("Could not open that conversation."));
		}
	}

	async function loadMore() {
		if (!state.conversation || !state.hasMore || !state.messages.length) return;
		const first = state.messages[0];
		const r = await api.getMessages(state.conversation, first.seq);
		state.messages = [...(r.messages || []), ...state.messages];
		state.hasMore = !!r.has_more;
	}

	async function send(text, context) {
		text = (text || "").trim();
		if (!text || state.sending) return false;
		if (state.status === "not_configured") {
			state.error = __("Chikku AI is not configured yet. Please ask your administrator to add an AI provider.");
			return false;
		}
		state.error = null;
		state.sending = true;
		state.greeting = null;
		const key = newKey();
		const attachments = state.pendingAttachments.slice();
		const temp = { name: tempId(), role: "user", content: text, status: "Sending", attachments, temp: true };
		state.messages.push(temp);
		try {
			const r = await api.sendMessage({
				text,
				conversation: state.conversation,
				context: state.contextEnabled ? context || null : null,
				attachments: attachments.map((a) => a.file),
				idempotency_key: key,
			});
			state.conversation = r.conversation;
			replaceMessage(temp.name, r.message);
			state.pendingAttachments = [];
			state.status = "busy";
			state.messages.push({ name: tempId(), role: "assistant", status: "Processing", placeholder: true });
			return true;
		} catch (e) {
			temp.status = "Failed";
			state.error = errorText(e, __("Your message could not be sent."));
			state.sending = false;
			return false;
		}
	}

	function replaceMessage(name, payload) {
		const i = state.messages.findIndex((m) => m.name === name);
		if (i >= 0) state.messages.splice(i, 1, payload);
		else state.messages.push(payload);
	}

	function placeholderIndex() {
		for (let i = state.messages.length - 1; i >= 0; i--) {
			if (state.messages[i].placeholder) return i;
		}
		return -1;
	}

	function handleEvent(evt) {
		if (!evt || !evt.type) return;
		if (evt.type === "action_done") {
			state.actions[evt.action_request] = { state: "done", result: evt };
			return;
		}
		if (handleModuleEvent(evt)) return;
		if (evt.conversation && evt.conversation !== state.conversation) return;
		const reply = evt.message;
		switch (evt.type) {
			case "status": {
				state.activeReply = reply;
				const i = placeholderIndex();
				if (i >= 0) state.messages[i].name = reply;
				break;
			}
			case "delta":
				state.activeReply = reply;
				state.streaming[reply] = (state.streaming[reply] || "") + (evt.text || "");
				break;
			case "tool":
				state.tools[reply] = evt.tool;
				break;
			case "action_token":
				state.actionTokens[evt.action_request] = evt.token;
				break;
			case "import_status":
			case "import_progress":
			case "transcript":
			case "speech":
			case "alert":
				break;
			case "done":
				replaceMessage(reply, evt.payload);
				delete state.streaming[reply];
				delete state.tools[reply];
				state.activeReply = null;
				state.sending = false;
				state.status = "ready";
				refreshConversations();
				break;
			case "error": {
				const i = placeholderIndex();
				const failed = { name: reply || tempId(), role: "assistant", status: "Failed", content_html: null,
					error_text: evt.text };
				if (i >= 0) state.messages.splice(i, 1, failed);
				state.sending = false;
				state.activeReply = null;
				state.status = "ready";
				break;
			}
		}
	}

	function imp(session) {
		if (!state.imports[session]) state.imports[session] = {};
		return state.imports[session];
	}

	function handleModuleEvent(evt) {
		if (evt.type === "import_status") {
			const s = imp(evt.session);
			s.status = evt.status;
			s.busy = false;
			if (evt.preview) s.block = evt.preview;
			if (evt.error) s.message = evt.error;
			for (const k of ["success", "failed", "error_file", "data_import"]) if (evt[k] !== undefined) s[k] = evt[k];
			if (evt.error && evt.status !== "Failed") s.status = "Mapping";
			return true;
		}
		if (evt.type === "import_progress") {
			for (const s of Object.values(state.imports)) {
				if (s.data_import && s.data_import === evt.data_import) {
					s.current = evt.current;
					s.total = evt.total;
				}
			}
			return true;
		}
		if (evt.type === "transcript") {
			if (evt.request !== state.voice.request) return true;
			state.voice.status = "idle";
			state.voice.request = null;
			if (evt.error) state.voice.error = evt.error;
			else state.voice.transcript = evt.text || "";
			return true;
		}
		if (evt.type === "speech") {
			state.speech.loading = null;
			if (evt.error) state.speech.error = evt.error;
			else Object.assign(state.speech, { message: evt.message, audio: evt.audio, error: null });
			return true;
		}
		if (evt.type === "alert") {
			state.capabilities.new_alerts = (state.capabilities.new_alerts || 0) + 1;
			return true;
		}
		return false;
	}

	async function importAction(kind, session, payload) {
		const s = imp(session);
		s.message = null;
		s.busy = true;
		try {
			if (kind === "validate" || kind === "template") {
				const block = await api.importUpdateMapping(session, payload);
				s.block = block;
				if (kind === "template") {
					await api.importSaveTemplate(session, `${block.doctype} import ${new Date().toISOString().slice(0, 10)}`);
					s.busy = false;
					s.message = null;
					return;
				}
				await api.importValidate(session);
				s.status = "Validating";
				return; // result arrives as an import_status event
			}
			if (kind === "back") {
				s.block = await api.importBackToMapping(session);
				s.status = "Mapping";
			} else if (kind === "confirm") {
				const req = await api.importRequestConfirmation(session);
				const res = await api.confirmAction(req.action_request, req.token);
				if (!res.ok) throw new Error(res.message || __("The import could not be started."));
				s.status = "Confirmed";
			} else if (kind === "retry") {
				const res = await api.importRetry(session);
				state.imports[res.session] = { block: res.mapping_block, status: "Mapping" };
				state.messages.push({ name: tempId(), role: "assistant", status: "Completed",
					content_html: `<p>${__("I prepared a new import with the {0} failed row(s). Review the mapping and validate again.", [res.rows_to_retry])}</p>`,
					blocks: [res.mapping_block] });
			} else if (kind === "cancel") {
				await api.importCancel(session);
				s.status = "Cancelled";
			}
		} catch (e) {
			s.message = errorText(e, __("Something went wrong."));
		}
		s.busy = false;
	}

	async function applyProposal(proposal) {
		state.proposals[proposal] = { state: "working" };
		try {
			const req = await api.workflowRequestApply(proposal);
			const res = await api.confirmAction(req.action_request, req.token);
			state.proposals[proposal] = res.ok ? { state: "done", result: res } : { state: "error", message: res.message };
		} catch (e) {
			state.proposals[proposal] = { state: "error", message: errorText(e, __("Could not create it.")) };
		}
	}

	async function sendAudio(blob) {
		state.voice.error = null;
		state.voice.transcript = null;
		state.voice.status = "transcribing";
		try {
			const r = await api.transcribe(blob);
			state.voice.request = r.request;
		} catch (e) {
			state.voice.status = "idle";
			state.voice.error = errorText(e, __("Voice input failed. Please type instead."));
		}
	}

	async function giveVoiceConsent() {
		await api.voiceConsent();
		state.capabilities.voice_consent = true;
	}

	async function speak(message) {
		state.speech.error = null;
		state.speech.loading = message;
		try {
			await api.speak(message);
		} catch (e) {
			state.speech.loading = null;
			state.speech.error = errorText(e, __("Could not read this out."));
		}
	}

	async function cancel() {
		if (!state.activeReply) return;
		try {
			await api.cancelTurn(state.activeReply);
		} catch (e) {
			/* the reply will still finish */
		}
	}

	async function setLanguage(lang) {
		if (!LANGUAGES.includes(lang)) return;
		const prev = state.language;
		state.language = lang;
		try {
			await api.setLanguage(lang);
		} catch (e) {
			state.language = prev;
			state.error = errorText(e, __("Could not save your language."));
		}
	}

	function validateFile(file) {
		const ext = ((file && file.name) || "").split(".").pop().toLowerCase();
		if (!ALLOWED_EXTENSIONS.includes(ext)) return __("This file type is not supported.");
		const max = (state.capabilities.max_upload_mb || 10) * 1024 * 1024;
		if (file.size > max) return __("The file is larger than {0} MB.", [state.capabilities.max_upload_mb || 10]);
		if (state.pendingAttachments.length >= 8) return __("You can attach up to 8 files.");
		return null;
	}

	async function attach(file) {
		const problem = validateFile(file);
		if (problem) {
			state.error = problem;
			return false;
		}
		state.uploading = true;
		state.error = null;
		try {
			const r = await api.upload(file, state.conversation);
			state.conversation = r.conversation;
			state.pendingAttachments.push(r);
			return true;
		} catch (e) {
			state.error = errorText(e, __("The file could not be uploaded."));
			return false;
		} finally {
			state.uploading = false;
		}
	}

	function removeAttachment(file) {
		state.pendingAttachments = state.pendingAttachments.filter((a) => a.file !== file);
	}

	async function confirmAction(name) {
		state.actions[name] = { state: "working" };
		try {
			let token = state.actionTokens[name];
			if (!token) {
				const re = await api.reissueAction(name);
				if (!re.ok) {
					state.actions[name] = { state: "error", message: re.message };
					return;
				}
				token = re.token;
			}
			const r = await api.confirmAction(name, token);
			delete state.actionTokens[name]; // single use
			state.actions[name] = r.ok ? { state: "done", result: r } : { state: "error", message: r.message };
		} catch (e) {
			state.actions[name] = { state: "error", message: errorText(e, __("Could not confirm.")) };
		}
	}

	async function cancelAction(name) {
		delete state.actionTokens[name];
		state.actions[name] = { state: "cancelled" };
		try {
			await api.cancelAction(name);
		} catch (e) {
			/* already expired or handled */
		}
	}

	function setOffline(offline) {
		if (offline) state.status = "offline";
		else if (state.status === "offline") state.status = state.sending ? "busy" : "ready";
	}

	return {
		state,
		setViewport,
		init,
		openPanel,
		closePanel,
		togglePanel,
		toggleExpand,
		newConversation,
		selectConversation,
		refreshConversations,
		loadMore,
		send,
		handleEvent,
		cancel,
		setLanguage,
		validateFile,
		attach,
		removeAttachment,
		confirmAction,
		cancelAction,
		setOffline,
		importAction,
		applyProposal,
		sendAudio,
		giveVoiceConsent,
		speak,
	};
}

export function errorText(e, fallback) {
	if (!e) return fallback;
	if (typeof e === "string") return e;
	if (e.message && typeof e.message === "string" && !e.message.startsWith("{")) return e.message;
	return fallback;
}
