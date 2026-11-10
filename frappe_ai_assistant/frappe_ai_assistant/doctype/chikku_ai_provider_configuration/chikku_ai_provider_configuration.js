// Copyright (c) 2026, Simpel and contributors
// For license information, please see license.txt

frappe.ui.form.on("Chikku AI Provider Configuration", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "frappe_ai_assistant.frappe_ai_assistant.api.admin.test_provider",
				args: { provider: frm.doc.name },
				freeze: true,
				freeze_message: __("Contacting provider..."),
				callback: (r) => {
					const res = r.message || {};
					frappe.msgprint({
						title: res.ok ? __("Connection OK") : __("Connection failed"),
						indicator: res.ok ? "green" : "red",
						message: res.ok
							? __("Model {0} answered in {1} ms.", [res.model || "", res.latency_ms])
							: __("Status: {0}", [res.status]),
					});
					frm.reload_doc();
				},
			});
		});
	},
});
