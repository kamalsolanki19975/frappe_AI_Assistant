// Chikku AI floating launcher. Kept tiny: the Vue chat panel is loaded only when first opened.
(function () {
	const DEFAULT_AVATAR =
		'<svg viewBox="0 0 40 40" width="100%" height="100%" aria-hidden="true"><defs><linearGradient id="caiG" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7c6cff"/><stop offset="1" stop-color="#3b82f6"/></linearGradient></defs><circle cx="20" cy="20" r="20" fill="url(#caiG)"/><path d="M20 9l2.6 6.4L29 18l-6.4 2.6L20 27l-2.6-6.4L11 18l6.4-2.6z" fill="#fff"/><circle cx="28.5" cy="27.5" r="2.2" fill="#fff" opacity=".85"/></svg>';

	function escapeAttr(s) {
		return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
	}

	function avatarHtml(cfg) {
		const url = cfg && cfg.avatar;
		if (url && /^(\/files\/|\/assets\/|https:\/\/)/.test(url)) {
			return `<img src="${escapeAttr(url)}" alt="" />`;
		}
		return DEFAULT_AVATAR;
	}

	let loading = null;

	function loadPanel(cfg) {
		if (window.chikku_ai_panel) return Promise.resolve(window.chikku_ai_panel);
		if (!loading) {
			loading = new Promise((resolve, reject) => {
				frappe.require("chikku_ai_panel.bundle.js", () => {
					try {
						resolve(window.chikku_ai.mountPanel({ avatarHtml: avatarHtml(cfg) }));
					} catch (e) {
						reject(e);
					}
				});
			});
			loading.catch(() => (loading = null));
		}
		return loading;
	}

	function isHiddenRoute() {
		const path = window.location.pathname || "";
		return /\/(printview|print|login)/.test(path);
	}

	function setup() {
		const cfg = window.frappe && frappe.boot && frappe.boot.chikku_ai;
		if (!cfg || !cfg.enabled || document.getElementById("chikku-ai-launcher") || isHiddenRoute()) return;

		const btn = document.createElement("button");
		btn.id = "chikku-ai-launcher";
		btn.type = "button";
		btn.className = "cai-launcher";
		btn.setAttribute("aria-label", cfg.assistant_name || "Chikku AI");
		btn.setAttribute("title", cfg.assistant_name || "Chikku AI");
		btn.innerHTML = `<span class="cai-launcher__avatar">${avatarHtml(cfg)}</span><span class="cai-launcher__label"></span>`;
		btn.querySelector(".cai-launcher__label").textContent = cfg.assistant_name || "Chikku AI";
		btn.addEventListener("click", async () => {
			btn.classList.add("cai-launcher--loading");
			try {
				const panel = await loadPanel(cfg);
				await panel.store.togglePanel();
			} catch (e) {
				frappe.show_alert({ message: __("Chikku AI could not load. Please refresh the page."), indicator: "red" });
			} finally {
				btn.classList.remove("cai-launcher--loading");
				btn.setAttribute("aria-expanded", window.chikku_ai_panel ? String(window.chikku_ai_panel.store.state.open) : "false");
			}
		});
		document.body.appendChild(btn);
	}

	if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
	else setup();
	if (window.jQuery) jQuery(document).on("app_ready", setup);
})();
