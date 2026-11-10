// Chikku AI Activity Centre: the user's own AI work, plus operational metadata for administrators.
frappe.pages["chikku-activity"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Chikku AI Activity"), single_column: true });
	new ChikkuActivity(page, wrapper);
};

const CA_STATUS_COLORS = {
	Completed: "green", Indexed: "green", Done: "green", Active: "green", Acknowledged: "green",
	"Applied (Inactive)": "blue", Validated: "blue", Mapping: "blue", Pending: "orange", Running: "orange",
	Importing: "orange", Validating: "orange", Indexing: "orange", Queued: "orange", Executing: "orange", New: "orange",
	"Partially Completed": "orange", Draft: "gray", Archived: "gray", Cancelled: "gray", Dismissed: "gray",
	Failed: "red", Expired: "red", Rejected: "red",
};

class ChikkuActivity {
	constructor(page, wrapper) {
		this.page = page;
		this.start = 0;
		this.$body = $(`<div class="cai-activity"></div>`).appendTo(page.main);
		this.kind = page.add_field({ fieldname: "kind", label: __("Type"), fieldtype: "Select",
			options: ["", "Conversation", "Report / Excel", "Import", "Knowledge", "Draft Action", "Workflow Proposal", "Alert"].join("\n"),
			change: () => this.refresh(true) });
		this.status = page.add_field({ fieldname: "status", label: __("Status"), fieldtype: "Select",
			options: ["", "completed", "failed"].join("\n"), change: () => this.refresh(true) });
		this.from = page.add_field({ fieldname: "from_date", label: __("From"), fieldtype: "Date", change: () => this.refresh(true) });
		this.to = page.add_field({ fieldname: "to_date", label: __("To"), fieldtype: "Date", change: () => this.refresh(true) });
		page.set_secondary_action(__("Refresh"), () => this.refresh(true), "refresh");
		this.refresh(true);
	}

	async refresh(reset) {
		if (reset) this.start = 0;
		const args = { kind: this.kind.get_value(), status: this.status.get_value(), from_date: this.from.get_value(),
			to_date: this.to.get_value(), start: this.start, page_length: 30 };
		if (this.user && this.user.get_value()) args.user = this.user.get_value();
		const r = await frappe.call({ method: "frappe_ai_assistant.frappe_ai_assistant.api.activity.list_activity", args, type: "GET" });
		const data = r.message || { rows: [] };
		if (data.is_admin && !this.user) {
			this.user = this.page.add_field({ fieldname: "user", label: __("User (admin)"), fieldtype: "Link", options: "User",
				change: () => this.refresh(true) });
			this.page.add_inner_button(__("Usage overview"), () => this.overview());
		}
		this.render(data, reset);
	}

	render(data, reset) {
		if (reset) this.$body.empty();
		this.$body.find(".cai-more").remove();
		if (!data.rows.length && reset) {
			this.$body.append(`<div class="text-muted" style="padding:40px;text-align:center">${__("No activity found.")}</div>`);
			return;
		}
		const viewingOther = data.user !== frappe.session.user;
		if (reset && viewingOther) {
			this.$body.append(`<div class="alert alert-info">${__("Showing operational metadata for {0}. Conversation contents are private.", [frappe.utils.escape_html(data.user)])}</div>`);
		}
		const $table = reset ? $(`<table class="table table-hover cai-activity-table"><thead><tr>
			<th>${__("Type")}</th><th>${__("Title")}</th><th>${__("Status")}</th><th>${__("Details")}</th><th>${__("Updated")}</th><th></th>
			</tr></thead><tbody></tbody></table>`).appendTo(this.$body) : this.$body.find("tbody").parent();
		const $tb = $table.find("tbody");
		for (const row of data.rows) {
			const color = CA_STATUS_COLORS[row.status] || "gray";
			const link = `/desk/${frappe.router.slug(row.doctype)}/${encodeURIComponent(row.name)}`;
			const details = Object.entries(row.detail || {}).filter(([, v]) => v !== null && v !== "" && v !== 0)
				.map(([k, v]) => k === "output_url" ? `<a href="${encodeURI(v)}" target="_blank">${__("Download")}</a>`
					: `<span class="text-muted">${frappe.utils.escape_html(frappe.model.unscrub(k))}:</span> ${frappe.utils.escape_html(String(v)).slice(0, 80)}`)
				.join(" · ");
			const $tr = $(`<tr>
				<td>${frappe.utils.escape_html(__(row.kind))}</td>
				<td>${viewingOther ? frappe.utils.escape_html(row.title) : `<a href="${link}">${frappe.utils.escape_html(row.title)}</a>`}</td>
				<td><span class="indicator-pill ${color}">${frappe.utils.escape_html(__(row.status || ""))}</span></td>
				<td class="small">${details}</td>
				<td class="small text-muted">${frappe.datetime.comment_when(row.modified)}</td>
				<td></td></tr>`).appendTo($tb);
			if (row.retry) {
				$(`<button class="btn btn-xs btn-default">${row.retry === "reindex" ? __("Re-index") : __("Retry")}</button>`)
					.appendTo($tr.find("td:last"))
					.on("click", () => this.retry(row));
			}
		}
		if (data.has_more) {
			$(`<div class="cai-more text-center"><button class="btn btn-default btn-sm">${__("Load more")}</button></div>`)
				.appendTo(this.$body).find("button").on("click", () => { this.start += 30; this.refresh(false); });
		}
	}

	retry(row) {
		const msg = row.retry === "import"
			? __("This prepares a new import containing only the failed records. You will validate and confirm it again in Chikku AI.")
			: __("Run this again?");
		frappe.confirm(msg, async () => {
			const r = await frappe.call({ method: "frappe_ai_assistant.frappe_ai_assistant.api.activity.retry", args: { kind: row.retry, name: row.name } });
			frappe.show_alert({ message: __("Done"), indicator: "green" });
			if (r.message && r.message.session) frappe.set_route("Form", "Chikku AI Import Session", r.message.session);
			this.refresh(true);
		});
	}

	async overview() {
		const r = await frappe.call({ method: "frappe_ai_assistant.frappe_ai_assistant.api.activity.admin_overview", type: "GET" });
		const d = r.message || {};
		const rows = (list, cols) => (list || []).map((x) => `<tr>${cols.map((c) => `<td>${frappe.utils.escape_html(String(x[c] ?? ""))}</td>`).join("")}</tr>`).join("");
		const html = `
			<h5>${__("Usage this month (since {0})", [d.since])}</h5>
			<table class="table table-sm"><tr><th>${__("User")}</th><th>${__("Requests")}</th><th>${__("Input tokens")}</th><th>${__("Output tokens")}</th><th>${__("Cost")}</th><th>${__("Errors")}</th></tr>
			${rows(d.by_user, ["user", "requests", "input_tokens", "output_tokens", "cost", "errors"])}</table>
			<h5>${__("Models")}</h5>
			<table class="table table-sm"><tr><th>${__("Provider")}</th><th>${__("Model")}</th><th>${__("Requests")}</th><th>${__("Errors")}</th></tr>
			${rows(d.by_model, ["provider", "model", "requests", "errors"])}</table>
			<h5>${__("Jobs (30 days)")}</h5>
			<table class="table table-sm"><tr><th>${__("Type")}</th><th>${__("Status")}</th><th>#</th></tr>${rows(d.jobs, ["job_type", "status", "n"])}</table>
			<h5>${__("Imports (30 days)")}</h5>
			<table class="table table-sm"><tr><th>${__("Status")}</th><th>#</th><th>${__("Imported")}</th><th>${__("Failed")}</th></tr>${rows(d.imports, ["status", "n", "ok", "failed"])}</table>
			<h5>${__("Security events")}</h5>
			<table class="table table-sm"><tr><th>${__("Event")}</th><th>${__("Severity")}</th><th>#</th></tr>${rows(d.security_events, ["event_type", "severity", "count"])}</table>`;
		const dlg = new frappe.ui.Dialog({ title: __("Chikku AI usage overview"), size: "extra-large", fields: [{ fieldtype: "HTML", fieldname: "h" }] });
		dlg.fields_dict.h.$wrapper.html(html);
		dlg.show();
	}
}
