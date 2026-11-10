<template>
	<div class="cai-block">
		<div v-if="block.type === 'doc_cards'" class="cai-cards">
			<a v-for="d in block.items" :key="d.doctype + d.name" class="cai-card" :href="docUrl(d)" target="_blank" rel="noopener">
				<span class="cai-card__type">{{ d.doctype }}</span>
				<span class="cai-card__name">{{ d.title || d.name }}</span>
				<span v-if="d.title && d.title !== d.name" class="cai-card__id">{{ d.name }}</span>
				<span v-if="d.status" class="cai-card__status">{{ d.status }}</span>
			</a>
		</div>

		<div v-else-if="block.type === 'table'" class="cai-table-block">
			<div v-if="block.title" class="cai-block__title">{{ block.title }}</div>
			<div class="cai-table-wrap">
				<table class="cai-table">
					<thead>
						<tr><th v-for="c in block.columns" :key="c">{{ label(c) }}</th></tr>
					</thead>
					<tbody>
						<tr v-for="(r, i) in block.rows" :key="i">
							<td v-for="(v, j) in r" :key="j">
								<a v-if="block.columns[j] === 'name' && block.doctype" :href="docUrl({ doctype: block.doctype, name: v })" target="_blank" rel="noopener">{{ v }}</a>
								<template v-else>{{ fmt(v) }}</template>
							</td>
						</tr>
					</tbody>
				</table>
			</div>
			<div v-if="block.truncated" class="cai-muted cai-small">{{ __("Showing the first rows only.") }}</div>
		</div>

		<div v-else-if="block.type === 'chart'" class="cai-chart-block">
			<div class="cai-block__title">{{ block.title }}</div>
			<div ref="chartEl" class="cai-chart"></div>
		</div>

		<div v-else-if="block.type === 'kpis'" class="cai-kpis">
			<div v-for="k in block.items" :key="k.label" class="cai-kpi">
				<div class="cai-kpi__label">{{ k.label }}</div>
				<div class="cai-kpi__value">{{ kpi(k) }}</div>
			</div>
		</div>

		<details v-else-if="block.type === 'source'" class="cai-source">
			<summary>{{ __("How this was calculated") }}</summary>
			<div v-if="block.trace">
				<div v-if="block.trace.method">{{ block.trace.method }}</div>
				<div v-if="block.trace.source_doctypes" class="cai-muted cai-small">{{ __("Source") }}: {{ block.trace.source_doctypes.join(", ") }}</div>
				<div v-if="block.trace.company" class="cai-muted cai-small">{{ __("Company") }}: {{ block.trace.company }}</div>
				<div v-if="block.trace.period || block.trace.as_of" class="cai-muted cai-small">{{ __("Period") }}: {{ block.trace.period || block.trace.as_of }}</div>
			</div>
			<div v-for="n in block.notes || []" :key="n" class="cai-muted cai-small">{{ n }}</div>
		</details>

		<a v-else-if="block.type === 'file'" class="cai-file" :href="safeUrl(block.url)" target="_blank" rel="noopener" download>
			<span class="cai-file__icon">XLSX</span>
			<span class="cai-file__body">
				<span class="cai-file__name">{{ block.file_name }}</span>
				<span class="cai-muted cai-small">{{ block.detail }} · {{ block.size_kb }} KB</span>
			</span>
			<span class="cai-file__dl">{{ __("Download") }}</span>
		</a>

		<div v-else-if="block.type === 'citations'" class="cai-citations">
			<div class="cai-block__title">{{ __("Sources") }}</div>
			<a v-for="c in block.items" :key="c.n" class="cai-citation" :href="docUrl({ doctype: 'Chikku AI Knowledge Document', name: c.document })" target="_blank" rel="noopener">
				<b>[{{ c.n }}]</b> {{ c.title }}<span v-if="c.heading" class="cai-muted"> — {{ c.heading }}</span><span v-if="c.page" class="cai-muted"> ({{ __("page") }} {{ c.page }})</span>
			</a>
		</div>

		<div v-else-if="block.type === 'excel_analysis'" class="cai-analysis">
			<div class="cai-block__title">{{ block.file }}</div>
			<div v-for="s in block.sheets" :key="s.name" class="cai-analysis__sheet">
				<div><b>{{ s.name }}</b> · {{ s.rows }} {{ __("rows") }} · {{ s.duplicate_rows }} {{ __("duplicate rows") }}<span v-if="s.formula_cells"> · {{ s.formula_cells }} {{ __("formulas") }}</span></div>
				<div class="cai-table-wrap">
					<table class="cai-table">
						<thead><tr><th>{{ __("Column") }}</th><th>{{ __("Type") }}</th><th>{{ __("Empty") }}</th><th>{{ __("Distinct") }}</th><th>{{ __("Notes") }}</th></tr></thead>
						<tbody>
							<tr v-for="c in s.columns.slice(0, 15)" :key="c.name">
								<td>{{ c.name }}</td><td>{{ c.type }}</td><td>{{ c.empty }}</td><td>{{ c.distinct }}</td>
								<td class="cai-small">
									<span v-if="c.sum !== undefined">Σ {{ fmt(c.sum) }}</span>
									<span v-if="c.invalid_gstin" class="cai-err"> {{ c.invalid_gstin }} {{ __("invalid GSTIN") }}</span>
									<span v-if="c.outliers" class="cai-warn"> {{ c.outliers.length }} {{ __("outliers") }}</span>
								</td>
							</tr>
						</tbody>
					</table>
				</div>
			</div>
			<div class="cai-muted cai-small">{{ block.note }}</div>
		</div>

		<div v-else-if="block.type === 'extraction'" class="cai-extraction">
			<div class="cai-block__title">{{ block.file }} · {{ block.method === 'pattern' ? __('pattern matching') : __('AI + pattern matching') }}</div>
			<div v-if="block.note" class="cai-notice cai-small">{{ block.note }}</div>
			<table class="cai-table">
				<tbody>
					<tr v-for="(f, key) in block.fields" :key="key" :class="{ 'cai-low': (block.low_confidence || []).includes(key) }">
						<td class="cai-muted">{{ label(key) }}</td>
						<td><b>{{ fmt(f.value) }}</b></td>
						<td class="cai-small">{{ conf(f.confidence) }}</td>
						<td class="cai-small cai-muted" :title="f.evidence">{{ (f.evidence || "").slice(0, 60) }}</td>
					</tr>
				</tbody>
			</table>
			<div v-if="block.items && block.items.length" class="cai-table-wrap">
				<table class="cai-table">
					<thead><tr><th>{{ __("Description") }}</th><th>{{ __("Qty") }}</th><th>{{ __("Rate") }}</th><th>{{ __("Amount") }}</th><th>{{ __("Matched Item") }}</th></tr></thead>
					<tbody>
						<tr v-for="(it, i) in block.items" :key="i">
							<td>{{ it.description || it.item_code }}</td><td>{{ fmt(it.qty) }}</td><td>{{ fmt(it.rate) }}</td><td>{{ fmt(it.amount) }}</td>
							<td :class="it.matched_item ? 'cai-ok' : 'cai-warn'">{{ it.matched_item || __("not matched") }}</td>
						</tr>
					</tbody>
				</table>
			</div>
			<div v-if="block.matches && block.matches.supplier" class="cai-small cai-ok">✓ {{ __("Supplier") }}: {{ block.matches.supplier }}<span v-if="block.matches.taxes_and_charges"> · {{ block.matches.taxes_and_charges }}</span></div>
			<ul v-if="block.issues && block.issues.length" class="cai-issues">
				<li v-for="(i, n) in block.issues" :key="n" :class="i.level === 'error' ? 'cai-err' : 'cai-warn'">{{ i.message }}</li>
			</ul>
		</div>

		<ImportStudio v-else-if="block.type === 'import_mapping' || block.type === 'import_preview'" :block="block" :store="store" />

		<div v-else-if="block.type === 'workflow_proposal'" class="cai-action">
			<div class="cai-action__head">
				<span class="cai-action__badge">{{ block.kind }}</span>
				<span class="cai-action__title">{{ block.title }} · {{ block.doctype }}</span>
			</div>
			<div v-if="block.kind === 'Workflow'" class="cai-flow">
				<div v-for="t in block.spec.transitions" :key="t.state + t.action" class="cai-flow__step">
					<span class="cai-flow__state">{{ t.state }}</span>
					<span class="cai-flow__arrow">— {{ t.action }} <small>({{ t.allowed }})</small> →</span>
					<span class="cai-flow__state">{{ t.next_state }}</span>
					<div v-if="t.condition" class="cai-small cai-muted">{{ __("if") }} {{ t.condition }}</div>
				</div>
			</div>
			<dl v-else class="cai-action__fields">
				<dt>{{ __("When") }}</dt><dd>{{ block.spec.event }}<span v-if="block.spec.days"> ({{ block.spec.days }} {{ __("days") }})</span></dd>
				<dt>{{ __("Condition") }}</dt><dd>{{ block.spec.condition || "—" }}</dd>
				<dt>{{ __("Notify") }}</dt><dd>{{ (block.spec.recipient_roles || []).join(", ") }}</dd>
			</dl>
			<ul v-if="block.issues.length" class="cai-issues">
				<li v-for="(i, n) in block.issues" :key="n" :class="i.level === 'error' ? 'cai-err' : 'cai-warn'">{{ i.message }}</li>
			</ul>
			<div v-if="wf.state === 'done'" class="cai-action__result cai-ok">{{ __("Created inactive:") }} {{ wf.result.name }} — {{ __("activate it in the standard screen after review.") }}</div>
			<div v-else-if="wf.state === 'error'" class="cai-action__result cai-err">{{ wf.message }}</div>
			<div v-else-if="block.can_apply" class="cai-action__buttons">
				<button class="cai-btn cai-btn--primary" :disabled="wf.state === 'working'" @click="store.applyProposal(block.proposal)">{{ __("Create (inactive)") }}</button>
				<span class="cai-muted cai-small">{{ __("Nothing is activated automatically.") }}</span>
			</div>
		</div>

		<ActionPreview v-else-if="block.type === 'action_preview'" :block="block" :store="store" />
	</div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import ActionPreview from "./ActionPreview.vue";
import ImportStudio from "./ImportStudio.vue";

const props = defineProps({ block: Object, store: Object });
const __ = window.__ || ((s) => s);
const chartEl = ref(null);
const wf = computed(() => (props.store.state.proposals || {})[props.block.proposal] || { state: "pending" });

function docUrl(d) {
	const slug = String(d.doctype || "").toLowerCase().replace(/ /g, "-");
	return `/desk/${encodeURIComponent(slug)}/${encodeURIComponent(d.name || "")}`;
}
function safeUrl(u) {
	return typeof u === "string" && u.startsWith("/private/files/") ? u : "#";
}
function label(c) {
	return String(c).replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}
function fmt(v) {
	if (v === null || v === undefined) return "";
	if (Array.isArray(v)) return v.join(", ");
	if (typeof v === "number" && window.format_number) return window.format_number(v, null, Number.isInteger(v) ? 0 : 2);
	return String(v);
}
function kpi(k) {
	if (typeof k.value !== "number") return k.value ?? "—";
	if (k.format === "currency" && window.format_currency) return window.format_currency(k.value, window.frappe?.boot?.sysdefaults?.currency);
	return fmt(k.value);
}
function conf(c) {
	if (c === "pattern") return __("pattern");
	if (typeof c === "number") return `${Math.round(c * 100)}%`;
	return "";
}

onMounted(() => {
	if (props.block.type === "chart" && chartEl.value && window.frappe?.Chart) {
		try {
			new window.frappe.Chart(chartEl.value, {
				data: { labels: props.block.labels, datasets: props.block.datasets },
				type: props.block.chart_type || "bar",
				height: 220,
				colors: ["#6d5dfc", "#3b82f6", "#14b8a6", "#f59e0b", "#ef4444"],
				axisOptions: { xIsSeries: props.block.chart_type === "line", shortenYAxisNumbers: 1 },
			});
		} catch (e) {
			/* the table/answer still carries the numbers */
		}
	}
});
</script>
