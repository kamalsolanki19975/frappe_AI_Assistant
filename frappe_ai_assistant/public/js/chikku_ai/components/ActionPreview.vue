<template>
	<div class="cai-action" :class="'cai-action--' + current.state">
		<div class="cai-action__head">
			<span class="cai-action__badge">{{ __("Draft") }}</span>
			<span class="cai-action__title">{{ block.preview.title || block.summary }}</span>
		</div>
		<dl class="cai-action__fields">
			<template v-for="f in block.preview.fields || []" :key="f.label">
				<dt>{{ f.label }}</dt>
				<dd>{{ f.value }}</dd>
			</template>
		</dl>
		<div v-if="block.preview.table" class="cai-table-wrap">
			<table class="cai-table">
				<thead>
					<tr><th v-for="c in block.preview.table.columns" :key="c">{{ c }}</th></tr>
				</thead>
				<tbody>
					<tr v-for="(r, i) in block.preview.table.rows" :key="i">
						<td v-for="(v, j) in r" :key="j">{{ v }}</td>
					</tr>
				</tbody>
			</table>
		</div>
		<ul v-if="block.preview.notes" class="cai-action__notes">
			<li v-for="n in block.preview.notes" :key="n">{{ n }}</li>
		</ul>

		<div v-if="current.state === 'done'" class="cai-action__result cai-ok">
			{{ __("Draft created:") }}
			<a :href="resultUrl" target="_blank" rel="noopener">{{ current.result.name }}</a>
		</div>
		<div v-else-if="current.state === 'error'" class="cai-action__result cai-err">
			{{ current.message || __("The draft could not be created.") }}
		</div>
		<div v-else-if="current.state === 'cancelled'" class="cai-action__result cai-muted">
			{{ __("Cancelled. Nothing was created.") }}
		</div>
		<div v-else class="cai-action__buttons">
			<button class="cai-btn cai-btn--primary" :disabled="current.state === 'working'" @click="store.confirmAction(block.action_request)">
				{{ current.state === "working" ? __("Creating…") : __("Create draft") }}
			</button>
			<button class="cai-btn" :disabled="current.state === 'working'" @click="store.cancelAction(block.action_request)">
				{{ __("Cancel") }}
			</button>
			<span class="cai-muted cai-small">{{ __("Expires in {0} minutes", [block.expiry_minutes]) }}</span>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({ block: Object, store: Object });
const __ = window.__ || ((s) => s);
const current = computed(() => props.store.state.actions[props.block.action_request] || { state: "pending" });
const resultUrl = computed(() => {
	const r = current.value.result || {};
	return `/desk/${encodeURIComponent(String(r.doctype || "").toLowerCase().replace(/ /g, "-"))}/${encodeURIComponent(r.name || "")}`;
});
</script>
