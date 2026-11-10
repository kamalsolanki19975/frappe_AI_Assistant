<template>
	<div class="cai-msg" :class="['cai-msg--' + message.role, { 'cai-msg--failed': failed }]">
		<div v-if="message.role !== 'user'" class="cai-msg__avatar" aria-hidden="true" v-html="avatarHtml"></div>
		<div class="cai-msg__body">
			<template v-if="message.role === 'user'">
				<div class="cai-bubble cai-bubble--user">{{ message.content }}</div>
				<div v-if="message.attachments && message.attachments.length" class="cai-chips cai-chips--right">
					<span v-for="a in message.attachments" :key="a.file" class="cai-chip">📎 {{ a.file_name }}</span>
				</div>
				<div v-if="message.status === 'Failed'" class="cai-small cai-err">{{ __("Not sent") }}</div>
			</template>

			<template v-else>
				<div v-if="isPending" class="cai-bubble cai-bubble--assistant">
					<div v-if="streamText" class="cai-stream">{{ streamText }}<span class="cai-caret"></span></div>
					<div v-else class="cai-typing" :aria-label="__('Chikku AI is thinking')"><i></i><i></i><i></i></div>
					<div v-if="toolName" class="cai-tool">
						<span class="cai-spinner"></span>{{ toolLabel }}
					</div>
				</div>
				<template v-else>
					<div v-if="message.content_html" class="cai-bubble cai-bubble--assistant cai-md" v-html="message.content_html"></div>
					<div v-else-if="message.error_text" class="cai-bubble cai-bubble--assistant cai-err">{{ message.error_text }}</div>
					<BlockView v-for="(b, i) in message.blocks || []" :key="i" :block="b" :store="store" />
					<div v-if="message.status === 'Completed'" class="cai-msg__meta">
						<button v-if="store.state.capabilities.voice_output && !String(message.name).startsWith('tmp-')" class="cai-icon-btn"
							:title="speaking ? __('Stop') : __('Read aloud')" :aria-label="speaking ? __('Stop') : __('Read aloud')" @click="toggleSpeak">
							<span v-if="store.state.speech.loading === message.name" class="cai-spinner"></span>
							<template v-else>{{ speaking ? "⏹" : "🔊" }}</template>
						</button>
						<button class="cai-icon-btn" :class="{ on: feedback === 'Helpful' }" :title="__('Helpful')" @click="rate('Helpful')">👍</button>
						<button class="cai-icon-btn" :class="{ on: feedback === 'Not Helpful' }" :title="__('Not helpful')" @click="rate('Not Helpful')">👎</button>
					</div>
				</template>
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import BlockView from "./BlockView.vue";

const props = defineProps({ message: Object, store: Object, api: Object, avatarHtml: String });
const __ = window.__ || ((s) => s);
const feedback = ref(props.message.feedback || null);

const isPending = computed(() => props.message.placeholder || ["Processing", "Queued"].includes(props.message.status));
const failed = computed(() => props.message.status === "Failed");
const streamText = computed(() => props.store.state.streaming[props.message.name] || "");
const toolName = computed(() => props.store.state.tools[props.message.name]);
const TOOL_LABELS = {
	search_documents: "Searching records…",
	get_document: "Reading the document…",
	get_current_context: "Looking at your screen…",
	search_customers: "Finding customers…",
	search_suppliers: "Finding suppliers…",
	search_items: "Finding items…",
	get_item_stock: "Checking stock…",
	summarise_records: "Calculating totals…",
	prepare_sales_order_draft: "Preparing a draft…",
	run_business_metric: "Calculating from SaarthiERP…",
	create_excel_report: "Building your Excel report…",
	list_attachments: "Looking at your files…",
	analyse_spreadsheet: "Analysing the spreadsheet…",
	compare_spreadsheets: "Comparing sheets…",
	modify_spreadsheet: "Preparing the revised workbook…",
	start_import: "Reading the file for import…",
	read_document: "Reading the document…",
	prepare_purchase_invoice_draft: "Preparing a draft invoice…",
	search_knowledge: "Searching company documents…",
	propose_workflow: "Designing the workflow…",
	propose_notification: "Designing the notification…",
	subscribe_alert: "Setting up your alert…",
};
const toolLabel = computed(() => __(TOOL_LABELS[toolName.value] || "Working…"));

const speaking = computed(() => props.store.state.speech.message === props.message.name);
function toggleSpeak() {
	if (speaking.value) {
		props.store.state.speech.message = null;
		window.chikku_ai_panel?.stopPlayback?.();
		return;
	}
	props.store.speak(props.message.name);
}

async function rate(value) {
	const next = feedback.value === value ? "" : value;
	feedback.value = next || null;
	try {
		await props.api.setFeedback(props.message.name, next);
	} catch (e) {
		/* feedback is optional */
	}
}
</script>
