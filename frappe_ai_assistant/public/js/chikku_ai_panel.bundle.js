// Chikku AI chat panel. Loaded on demand by chikku_ai.bundle.js when the launcher is first used.
import { createApp, h, ref } from "vue";
import App from "./chikku_ai/App.vue";
import { api } from "./chikku_ai/lib/api.js";
import { getDeskContext } from "./chikku_ai/lib/context.js";
import { createStore } from "./chikku_ai/lib/store.js";

function mountPanel({ avatarHtml }) {
	if (window.chikku_ai_panel) return window.chikku_ai_panel;
	const store = createStore({
		api,
		storage: window.sessionStorage,
		user: frappe.session.user,
		translate: window.__,
	});
	const host = document.createElement("div");
	host.id = "chikku-ai-panel-root";
	document.body.appendChild(host);

	const appRef = ref(null);
	const app = createApp({
		render: () =>
			h(App, {
				ref: appRef,
				store,
				api,
				avatarHtml,
				getContext: () => getDeskContext({ frappe: window.frappe, cur_frm: window.cur_frm, cur_list: window.cur_list }),
			}),
	});
	app.mount(host);

	frappe.realtime.on("chikku_ai_event", (evt) => store.handleEvent(evt));
	// Native Data Import progress (published to the Data Import's own room) -> import progress bars.
	frappe.realtime.on("data_import_progress", (d) =>
		store.handleEvent({ type: "import_progress", data_import: d.data_import, current: d.current, total: d.total }));
	const watchImports = () => {
		for (const s of Object.values(store.state.imports)) {
			if (s.data_import && !s.subscribed) {
				frappe.realtime.doc_subscribe("Data Import", s.data_import);
				s.subscribed = true;
			}
		}
	};
	setInterval(watchImports, 1500);
	const setViewport = () => store.setViewport(window.innerWidth);
	setViewport();
	window.addEventListener("resize", setViewport, { passive: true });
	window.addEventListener("online", () => store.setOffline(false));
	window.addEventListener("offline", () => store.setOffline(true));

	window.chikku_ai_panel = { store, app, stopPlayback: () => appRef.value?.stopPlayback?.() };
	return window.chikku_ai_panel;
}

window.chikku_ai = window.chikku_ai || {};
window.chikku_ai.mountPanel = mountPanel;
