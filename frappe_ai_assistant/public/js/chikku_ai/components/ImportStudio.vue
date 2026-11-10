<template>
	<div class="cai-import">
		<div class="cai-import__head">
			<span class="cai-action__badge">{{ __("Import") }}</span>
			<b>{{ view.doctype }}</b>
			<span class="cai-muted cai-small">· {{ statusLabel }}</span>
		</div>

		<!-- 1. mapping -->
		<template v-if="stage === 'mapping'">
			<div class="cai-import__row">
				<label class="cai-small">{{ __("Import type") }}
					<select v-model="importType" class="cai-select">
						<option v-for="m in view.modes || [view.import_type]" :key="m" :value="m">{{ __(m) }}</option>
					</select>
				</label>
				<label class="cai-small" :title="__('Rows with the same value form one document with several child rows')">{{ __("Group rows by") }}
					<select v-model="parentKey" class="cai-select">
						<option value="">{{ __("— one record per row —") }}</option>
						<option v-for="c in columns" :key="c.index" :value="c.header">{{ c.header }}</option>
					</select>
				</label>
			</div>
			<div class="cai-table-wrap cai-import__grid">
				<table class="cai-table">
					<thead><tr><th>{{ __("Column in file") }}</th><th>{{ __("Sample") }}</th><th>{{ __("SaarthiERP field") }}</th></tr></thead>
					<tbody>
						<tr v-for="c in columns" :key="c.index" :class="{ 'cai-low': c.field && c.confidence < 0.8 }">
							<td>{{ c.header }}</td>
							<td class="cai-small cai-muted">{{ (c.samples || []).join(", ").slice(0, 40) }}</td>
							<td>
								<select v-model="c.field" class="cai-select" :aria-label="__('Field for') + ' ' + c.header">
									<option :value="null">{{ __("— don't import —") }}</option>
									<option v-for="f in view.fields" :key="f.value" :value="f.value">{{ f.label }}{{ f.reqd ? " *" : "" }}</option>
								</select>
							</td>
						</tr>
					</tbody>
				</table>
			</div>
			<div class="cai-import__defaults">
				<div class="cai-small cai-muted">{{ __("Default values (used when a cell is empty)") }}</div>
				<div v-for="(v, k) in defaults" :key="k" class="cai-import__default">
					<span class="cai-small">{{ fieldLabel(k) }}</span>
					<input v-model="defaults[k]" class="cai-input-sm" />
					<button class="cai-icon-btn cai-small" @click="removeDefault(k)">✕</button>
				</div>
				<div class="cai-import__default">
					<select v-model="newDefault" class="cai-select">
						<option value="">{{ __("+ add a default value") }}</option>
						<option v-for="f in parentFields" :key="f.value" :value="f.value">{{ f.label }}</option>
					</select>
					<button class="cai-btn" :disabled="!newDefault" @click="addDefault">{{ __("Add") }}</button>
				</div>
			</div>
			<div v-if="missing.length" class="cai-notice cai-small">
				{{ __("Required fields not mapped yet:") }} {{ missing.map((m) => m.label).join(", ") }}
			</div>
			<div class="cai-action__buttons">
				<button class="cai-btn cai-btn--primary" :disabled="busy" @click="validate">{{ busy ? __("Working…") : __("Validate") }}</button>
				<button class="cai-btn" :disabled="busy" @click="saveTemplate">{{ __("Save as template") }}</button>
				<button class="cai-btn" :disabled="busy" @click="store.importAction('cancel', view.session)">{{ __("Cancel") }}</button>
			</div>
		</template>

		<!-- 2. validating -->
		<div v-else-if="stage === 'validating'" class="cai-center cai-small"><span class="cai-spinner"></span>&nbsp;{{ __("Checking every row against SaarthiERP…") }}</div>

		<!-- 3. preview -->
		<template v-else-if="stage === 'preview'">
			<div class="cai-kpis">
				<div class="cai-kpi"><div class="cai-kpi__label">{{ __("Rows") }}</div><div class="cai-kpi__value">{{ view.total_rows }}</div></div>
				<div class="cai-kpi"><div class="cai-kpi__label">{{ __("Ready") }}</div><div class="cai-kpi__value cai-ok">{{ view.valid_records }}</div></div>
				<div class="cai-kpi"><div class="cai-kpi__label">{{ __("Invalid rows") }}</div><div class="cai-kpi__value cai-err">{{ view.invalid_rows }}</div></div>
				<div class="cai-kpi"><div class="cai-kpi__label">{{ __("Duplicates") }}</div><div class="cai-kpi__value cai-warn">{{ view.duplicate_rows }}</div></div>
			</div>
			<ul class="cai-issues"><li v-for="w in view.warnings || []" :key="w" class="cai-warn">{{ w }}</li></ul>
			<div v-if="view.errors && view.errors.length" class="cai-table-wrap cai-import__grid">
				<table class="cai-table">
					<thead><tr><th>{{ __("Row") }}</th><th>{{ __("Field") }}</th><th>{{ __("Problem") }}</th></tr></thead>
					<tbody><tr v-for="(e, i) in view.errors.slice(0, 100)" :key="i"><td>{{ e.row }}</td><td>{{ e.field }}</td><td>{{ e.message }}</td></tr></tbody>
				</table>
			</div>
			<div class="cai-small cai-muted">
				{{ __("Invalid and duplicate rows are not imported; nothing is dropped silently.") }}
				<span v-if="view.dry_run_tested">{{ __("{0} record(s) were test-saved to run SaarthiERP validations, then rolled back.", [view.dry_run_tested]) }}</span>
				{{ __("Estimated time: about {0} seconds.", [view.estimated_seconds]) }}
			</div>
			<details v-if="view.sample && view.sample.length" class="cai-source"><summary>{{ __("Sample records") }}</summary>
				<pre class="cai-small">{{ JSON.stringify(view.sample, null, 1) }}</pre>
			</details>
			<div class="cai-action__buttons">
				<button class="cai-btn cai-btn--primary" :disabled="busy || !view.valid_records" @click="store.importAction('confirm', view.session)">
					{{ busy ? __("Starting…") : __("Import {0} record(s)", [view.valid_records]) }}
				</button>
				<button class="cai-btn" :disabled="busy" @click="store.importAction('back', view.session)">{{ __("Change mapping") }}</button>
			</div>
		</template>

		<!-- 4. running / done -->
		<div v-else-if="stage === 'running'" class="cai-import__progress">
			<div class="cai-progress"><div class="cai-progress__bar" :style="{ width: progressPct + '%' }"></div></div>
			<div class="cai-small cai-muted">{{ progressText }}</div>
		</div>
		<div v-else-if="stage === 'done'">
			<div :class="state.status === 'Completed' ? 'cai-ok' : state.status === 'Failed' ? 'cai-err' : 'cai-warn'">
				<b>{{ __(state.status) }}</b> — {{ __("{0} imported, {1} failed", [state.success || 0, state.failed || 0]) }}
			</div>
			<div v-if="state.error" class="cai-err cai-small">{{ state.error }}</div>
			<div class="cai-action__buttons">
				<a v-if="state.error_file" class="cai-btn" :href="state.error_file" target="_blank">{{ __("Download error report") }}</a>
				<button v-if="state.failed" class="cai-btn" :disabled="busy" @click="store.importAction('retry', view.session)">{{ __("Retry failed rows") }}</button>
			</div>
		</div>
		<div v-if="state.message" class="cai-err cai-small">{{ state.message }}</div>
	</div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";

const props = defineProps({ block: Object, store: Object });
const __ = window.__ || ((s, a) => (a ? s.replace(/\{(\d+)\}/g, (_, i) => a[i]) : s));

const state = computed(() => props.store.state.imports?.[props.block.session] || {});
const view = computed(() => state.value.block || props.block);
const busy = computed(() => !!state.value.busy);
const stage = computed(() => {
	const s = state.value.status || view.value.status;
	if (["Importing", "Confirmed"].includes(s)) return "running";
	if (["Completed", "Partially Completed", "Failed"].includes(s)) return "done";
	if (s === "Validating") return "validating";
	if (view.value.type === "import_preview" && s === "Validated") return "preview";
	return "mapping";
});
const statusLabel = computed(() => __(state.value.status || view.value.status || "Mapping"));
const columns = reactive((view.value.columns || []).map((c) => ({ ...c })));
const defaults = reactive({ ...(view.value.defaults || {}) });
const importType = ref(view.value.import_type);
const parentKey = ref(view.value.parent_key_column || "");
const newDefault = ref("");
watch(view, (v) => {
	if (v.type === "import_mapping" && v.columns) {
		columns.splice(0, columns.length, ...v.columns.map((c) => ({ ...c })));
	}
});

const parentFields = computed(() => (view.value.fields || []).filter((f) => !f.table && f.value !== "name"));
const missing = computed(() => {
	const mapped = new Set(columns.map((c) => c.field).filter(Boolean));
	return (view.value.missing_mandatory || []).filter((m) => !mapped.has(m.value) && !(m.value in defaults));
});
const progressPct = computed(() => (state.value.total ? Math.round((state.value.current / state.value.total) * 100) : 10));
const progressText = computed(() => state.value.total ? __("Imported {0} of {1}", [state.value.current, state.value.total]) : __("Importing in the background…"));

function fieldLabel(k) {
	return ((view.value.fields || []).find((f) => f.value === k) || {}).label || k;
}
function addDefault() {
	if (newDefault.value && !(newDefault.value in defaults)) defaults[newDefault.value] = "";
	newDefault.value = "";
}
function removeDefault(k) {
	delete defaults[k];
}
function payload() {
	return { mapping: columns.map((c) => ({ index: c.index, field: c.field || null, samples: c.samples })),
		defaults: { ...defaults }, import_type: importType.value, parent_key_column: parentKey.value || null };
}
function validate() {
	props.store.importAction("validate", view.value.session, payload());
}
function saveTemplate() {
	props.store.importAction("template", view.value.session, payload());
}
</script>
