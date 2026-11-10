<template>
	<transition name="cai-pop">
		<section
			v-if="s.open"
			class="cai-panel"
			:class="{ 'cai-panel--expanded': s.expanded, 'cai-panel--mobile': s.layout === 'mobile' }"
			role="dialog"
			aria-modal="false"
			:aria-label="s.assistant.name"
			@keydown.esc.stop="store.closePanel()"
		>
			<header class="cai-header">
				<div class="cai-header__id">
					<div class="cai-avatar" aria-hidden="true" v-html="avatarHtml"></div>
					<div>
						<div class="cai-header__name">{{ s.assistant.name }}</div>
						<div class="cai-status" :class="'cai-status--' + s.status">
							<span class="cai-dot"></span>{{ statusLabel }}
						</div>
					</div>
				</div>
				<div class="cai-header__actions">
					<select class="cai-lang" :value="s.language" :aria-label="__('Language')" @change="store.setLanguage($event.target.value)">
						<option value="Auto">{{ __("Auto") }}</option>
						<option value="English">English</option>
						<option value="Hindi">हिन्दी</option>
						<option value="Hinglish">Hinglish</option>
					</select>
					<a class="cai-icon-btn cai-badge-wrap" href="/desk/chikku-activity" :title="__('Activity Centre')" :aria-label="__('Activity Centre')">
						<svg viewBox="0 0 24 24" width="18" height="18"><path d="M3 12h4l3-8 4 16 3-8h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
						<span v-if="s.capabilities.new_alerts" class="cai-badge">{{ s.capabilities.new_alerts }}</span>
					</a>
					<button class="cai-icon-btn" :title="__('Conversations')" :aria-label="__('Conversations')" @click="toggleHistory">
						<svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>
					</button>
					<button class="cai-icon-btn" :title="__('New conversation')" :aria-label="__('New conversation')" @click="store.newConversation()">
						<svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>
					</button>
					<button v-if="s.layout !== 'mobile'" class="cai-icon-btn" :title="s.expanded ? __('Collapse') : __('Expand')" :aria-label="s.expanded ? __('Collapse') : __('Expand')" @click="store.toggleExpand()">
						<svg v-if="!s.expanded" viewBox="0 0 24 24" width="18" height="18"><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>
						<svg v-else viewBox="0 0 24 24" width="18" height="18"><path d="M4 14h6v6M20 10h-6V4M10 14l-7 7M14 10l7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>
					</button>
					<button class="cai-icon-btn" :title="__('Close')" :aria-label="__('Close')" @click="store.closePanel()">
						<svg viewBox="0 0 24 24" width="18" height="18"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>
					</button>
				</div>
			</header>

			<div v-if="s.view === 'history'" class="cai-history">
				<div class="cai-history__title">{{ __("Recent conversations") }}</div>
				<button v-for="c in s.conversations" :key="c.name" class="cai-history__item" :class="{ active: c.name === s.conversation }" @click="store.selectConversation(c.name)">
					<span class="cai-history__name">{{ c.title || __("Untitled") }}</span>
					<span class="cai-muted cai-small">{{ prettyDate(c.last_message_at) }}</span>
				</button>
				<div v-if="!s.conversations.length" class="cai-muted cai-empty">{{ __("No conversations yet.") }}</div>
			</div>

			<div v-else ref="scroller" class="cai-body" aria-live="polite" @scroll="onScroll">
				<div v-if="s.loading" class="cai-center"><span class="cai-spinner"></span></div>
				<button v-if="s.hasMore" class="cai-link cai-center" @click="store.loadMore()">{{ __("Load earlier messages") }}</button>

				<div v-if="!s.messages.length && !s.loading" class="cai-welcome">
					<div class="cai-welcome__avatar" aria-hidden="true" v-html="avatarHtml"></div>
					<p v-if="s.greeting" class="cai-welcome__greeting">{{ s.greeting }}</p>
					<p v-else class="cai-welcome__greeting">{{ s.assistant.description || __("How can I help you today?") }}</p>
					<div v-if="s.status === 'not_configured'" class="cai-notice">
						{{ __("Chikku AI isn't connected to an AI provider yet. An administrator can set this up in Chikku AI Settings.") }}
					</div>
					<div class="cai-suggestions">
						<button v-for="p in prompts" :key="p.prompt" class="cai-suggestion" @click="sendText(p.prompt)">{{ p.prompt }}</button>
					</div>
				</div>

				<MessageItem v-for="m in s.messages" :key="m.name" :message="m" :store="store" :api="api" :avatar-html="avatarHtml" />
			</div>

			<div v-if="s.error" class="cai-error" role="alert">
				<span>{{ s.error }}</span>
				<button class="cai-icon-btn" :aria-label="__('Dismiss')" @click="s.error = null">✕</button>
			</div>

			<footer v-if="s.view === 'chat'" class="cai-composer">
				<div v-if="context && s.capabilities.context" class="cai-context" :class="{ off: !s.contextEnabled }">
					<span class="cai-context__icon">📄</span>
					<span class="cai-context__label">{{ context.label }}</span>
					<button class="cai-icon-btn cai-small" :title="s.contextEnabled ? __('Ignore this page') : __('Use this page as context')" @click="s.contextEnabled = !s.contextEnabled">
						{{ s.contextEnabled ? "✕" : "+" }}
					</button>
				</div>
				<div v-if="s.pendingAttachments.length" class="cai-chips">
					<span v-for="a in s.pendingAttachments" :key="a.file" class="cai-chip">
						📎 {{ a.file_name }}
						<button class="cai-chip__x" :aria-label="__('Remove')" @click="store.removeAttachment(a.file)">✕</button>
					</span>
				</div>
				<div v-if="consentOpen" class="cai-consent" role="dialog" :aria-label="__('Voice notice')">
					<p class="cai-small">{{ __("Your recording is sent to the configured speech service to turn it into text. Chikku AI does not store the audio; it is deleted right after transcription. You can edit the text before sending.") }}</p>
					<button class="cai-btn cai-btn--primary" @click="acceptConsent">{{ __("I agree, start") }}</button>
					<button class="cai-btn" @click="consentOpen = false">{{ __("Not now") }}</button>
				</div>
				<div v-if="recording" class="cai-recbar" role="status">
					<span class="cai-recdot"></span>{{ __("Listening…") }} {{ recSeconds }}s / {{ s.capabilities.voice_max_seconds }}s
					<button class="cai-link" @click="cancelRecording">{{ __("Cancel") }}</button>
				</div>
				<div v-if="s.voice.error" class="cai-small cai-err">{{ s.voice.error }}</div>
				<div class="cai-input">
					<input ref="fileInput" type="file" class="cai-hidden" multiple :accept="accept" @change="onFiles" />
					<button v-if="s.capabilities.attachments" class="cai-icon-btn" :disabled="s.uploading" :title="__('Attach a file or image')" :aria-label="__('Attach a file or image')" @click="fileInput.click()">
						<span v-if="s.uploading" class="cai-spinner"></span>
						<svg v-else viewBox="0 0 24 24" width="18" height="18"><path d="M21 11.5l-8.5 8.5a5 5 0 01-7-7L14 4.5a3.5 3.5 0 015 5L10.5 18a2 2 0 01-3-3L15 7.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/></svg>
					</button>
					<textarea
						ref="textarea"
						v-model="draft"
						rows="1"
						class="cai-textarea"
						:placeholder="placeholder"
						:aria-label="__('Message')"
						@keydown.enter.exact.prevent="submit"
						@input="autosize"
					></textarea>
					<button
						v-if="s.capabilities.voice_input"
						class="cai-icon-btn cai-mic"
						:class="{ 'cai-mic--rec': recording, 'cai-mic--busy': s.voice.status === 'transcribing' }"
						:disabled="s.voice.status === 'transcribing'"
						:title="recording ? __('Stop and transcribe') : __('Speak')"
						:aria-label="recording ? __('Stop and transcribe') : __('Speak')"
						:aria-pressed="recording"
						@click="toggleRecording"
					>
						<span v-if="s.voice.status === 'transcribing'" class="cai-spinner"></span>
						<svg v-else viewBox="0 0 24 24" width="18" height="18"><rect x="9" y="3" width="6" height="11" rx="3" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M5 11a7 7 0 0014 0M12 18v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/></svg>
					</button>
					<button v-if="s.sending && s.activeReply" class="cai-send cai-send--stop" :title="__('Stop')" :aria-label="__('Stop')" @click="store.cancel()">
						<svg viewBox="0 0 24 24" width="16" height="16"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>
					</button>
					<button v-else class="cai-send" :disabled="!draft.trim() || s.sending" :title="__('Send')" :aria-label="__('Send')" @click="submit">
						<svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 12l16-8-6 16-2.5-6.5L4 12z" fill="currentColor"/></svg>
					</button>
				</div>
				<div class="cai-footnote">{{ __("Chikku AI can make mistakes. Check important details.") }}</div>
			</footer>
		</section>
	</transition>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import MessageItem from "./components/MessageItem.vue";
import { ALLOWED_EXTENSIONS } from "./lib/store.js";

const props = defineProps({ store: Object, api: Object, getContext: Function, avatarHtml: String });
const store = props.store;
const s = store.state;
const __ = window.__ || ((t) => t);

const draft = ref("");
const scroller = ref(null);
const textarea = ref(null);
const fileInput = ref(null);
const context = ref(null);
const stickToBottom = ref(true);
const accept = ALLOWED_EXTENSIONS.map((e) => "." + e).join(",");

const statusLabel = computed(() => ({
	ready: __("Online"),
	busy: __("Thinking…"),
	not_configured: __("Not configured"),
	offline: __("Offline"),
	error: __("Unavailable"),
})[s.status] || "");

const placeholder = computed(() =>
	s.language === "Hindi" ? "अपना सवाल लिखें…" : s.language === "Hinglish" ? "Apna sawaal likhiye…" : __("Ask anything about your ERP…")
);

const prompts = computed(() => {
	const dt = context.value && context.value.doctype;
	const lang = s.language;
	return (s.suggested || [])
		.filter((p) => !p.doctype || p.doctype === dt)
		.filter((p) => !p.language || p.language === "All" || lang === "Auto" || p.language === lang)
		.slice(0, 4);
});

function refreshContext() {
	try {
		context.value = props.getContext();
	} catch (e) {
		context.value = null;
	}
}

async function submit() {
	await sendText(draft.value);
}

async function sendText(text) {
	refreshContext();
	const ok = await store.send(text, context.value);
	if (ok) {
		draft.value = "";
		await nextTick();
		autosize();
	}
}

function autosize() {
	const el = textarea.value;
	if (!el) return;
	el.style.height = "auto";
	el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

async function onFiles(e) {
	const files = Array.from(e.target.files || []);
	for (const f of files) await store.attach(f);
	e.target.value = "";
}

function toggleHistory() {
	s.view = s.view === "history" ? "chat" : "history";
	if (s.view === "history") store.refreshConversations();
}

function onScroll() {
	const el = scroller.value;
	if (!el) return;
	stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

function scrollDown() {
	nextTick(() => {
		const el = scroller.value;
		if (el && stickToBottom.value) el.scrollTop = el.scrollHeight;
	});
}

function prettyDate(d) {
	try {
		return d && window.comment_when ? window.comment_when(d) : d || "";
	} catch (e) {
		return d || "";
	}
}

watch(() => s.messages.length, scrollDown);
watch(() => JSON.stringify(s.streaming).length, scrollDown);
watch(
	() => s.open,
	(open) => {
		if (open) {
			refreshContext();
			stickToBottom.value = true;
			scrollDown();
			nextTick(() => textarea.value && textarea.value.focus());
		}
	},
	{ immediate: true }
);

// ---------------------------------------------------------------- voice (push-to-talk)
const recording = ref(false);
const recSeconds = ref(0);
const consentOpen = ref(false);
let recorder = null;
let chunks = [];
let timer = null;
let discard = false;
let player = null;

async function toggleRecording() {
	if (recording.value) return stopRecording();
	if (!s.capabilities.voice_consent) {
		consentOpen.value = true;
		return;
	}
	startRecording();
}
async function acceptConsent() {
	consentOpen.value = false;
	try {
		await store.giveVoiceConsent();
		startRecording();
	} catch (e) {
		s.voice.error = __("Could not save your choice.");
	}
}
async function startRecording() {
	stopPlayback(); // interruption: speaking stops any reply being read out
	s.voice.error = null;
	if (!navigator.mediaDevices || !window.MediaRecorder) {
		s.voice.error = __("This browser does not support voice recording.");
		return;
	}
	let stream;
	try {
		stream = await navigator.mediaDevices.getUserMedia({ audio: true });
	} catch (e) {
		s.voice.error = __("Microphone access was blocked. Allow it in the browser to use voice.");
		return;
	}
	chunks = [];
	discard = false;
	const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
	recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
	recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
	recorder.onstop = () => {
		stream.getTracks().forEach((t) => t.stop());
		clearInterval(timer);
		recording.value = false;
		if (!discard && chunks.length) store.sendAudio(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
		chunks = [];
	};
	recorder.start();
	recording.value = true;
	recSeconds.value = 0;
	timer = setInterval(() => {
		recSeconds.value += 1;
		if (recSeconds.value >= (s.capabilities.voice_max_seconds || 30)) stopRecording();
	}, 1000);
}
function stopRecording() {
	if (recorder && recorder.state !== "inactive") recorder.stop();
}
function cancelRecording() {
	discard = true;
	stopRecording();
}
watch(() => s.voice.transcript, (t) => {
	if (t === null || t === undefined) return;
	draft.value = draft.value ? `${draft.value} ${t}` : t;
	s.voice.transcript = null;
	nextTick(() => { autosize(); textarea.value && textarea.value.focus(); });
});
function stopPlayback() {
	if (player) {
		player.pause();
		player = null;
	}
	s.speech.message = null;
}
watch(() => s.speech.audio, (src) => {
	if (!src) return;
	stopPlayback();
	player = new Audio(src);
	const playing = s.speech.message;
	s.speech.audio = null;
	s.speech.message = playing;
	player.onended = () => { s.speech.message = null; };
	player.play().catch(() => { s.speech.error = __("Playback was blocked by the browser."); });
});

defineExpose({ refreshContext, stopPlayback });
</script>
