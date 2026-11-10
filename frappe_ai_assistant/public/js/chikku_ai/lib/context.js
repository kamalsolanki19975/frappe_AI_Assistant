// Builds the Desk context hint sent with a message. The server re-validates everything;
// this is only a hint about what the user is looking at.

const MAX_SELECTED = 50;

export function getDeskContext(env) {
	const frappe = env.frappe;
	let route = [];
	try {
		route = (frappe.get_route && frappe.get_route()) || [];
	} catch (e) {
		route = [];
	}
	route = route.filter((p) => typeof p === "string").slice(0, 6);
	const kind = (route[0] || "").toLowerCase();

	if (kind === "form") {
		const frm = env.cur_frm;
		const doctype = (frm && frm.doctype) || route[1];
		const docname = (frm && frm.docname) || route[2];
		if (!doctype || !docname) return null;
		if (frm && frm.is_new && frm.is_new()) return { kind: "form", doctype, docname: null, label: doctype };
		return { kind: "form", route, doctype, docname, label: `${doctype} ${docname}` };
	}
	if (kind === "list" || kind === "report") {
		const list = env.cur_list;
		const doctype = (list && list.doctype) || route[1];
		if (!doctype) return null;
		let selected = [];
		try {
			if (list && list.get_checked_items) selected = list.get_checked_items(true) || [];
		} catch (e) {
			selected = [];
		}
		selected = selected.filter((s) => typeof s === "string").slice(0, MAX_SELECTED);
		const label = selected.length ? `${doctype} (${selected.length} selected)` : `${doctype} list`;
		return { kind, route, doctype, selected, label };
	}
	return null;
}
