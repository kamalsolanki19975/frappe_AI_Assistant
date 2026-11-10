"""Administrator endpoints (Chikku AI Manager / System Manager)."""

from __future__ import annotations

import time

import frappe
from frappe.utils import cstr, get_first_day, now_datetime, nowdate

from frappe_ai_assistant.frappe_ai_assistant import AUDIT_LOG, PROVIDER, USAGE_LOG
from frappe_ai_assistant.frappe_ai_assistant.audit.logger import log_usage
from frappe_ai_assistant.frappe_ai_assistant.providers import router
from frappe_ai_assistant.frappe_ai_assistantproviders.base import ProviderError
from frappe_ai_assistant.frappe_ai_assistant.security.access import require_admin
from frappe_ai_assistant.frappe_ai_assistant.security.limits import check_simple_rate


@frappe.whitelist(methods=["POST"])
def test_provider(provider: str) -> dict:
	"""Sends a tiny request to the provider. Returns status only; never echoes credentials."""
	require_admin()
	check_simple_rate("provider_test", 10, 3600)
	provider = cstr(provider)[:140]
	started = time.monotonic()
	try:
		cfg = router.load_config(provider)
		res = router.get_adapter(cfg).chat([{"role": "user", "content": "Reply with the single word OK."}],
										   None, max_tokens=10, temperature=0, stream=False)
		ok, status, model = True, "OK", res.model or cfg.model
		log_usage(user=frappe.session.user, provider=provider, model=model, slot="test", status="Success",
				  input_tokens=res.input_tokens, output_tokens=res.output_tokens,
				  latency_ms=int((time.monotonic() - started) * 1000))
	except ProviderError as e:
		ok, status, model = False, f"Failed: {e.category}", None
	if frappe.db.exists(PROVIDER, provider):
		frappe.db.set_value(PROVIDER, provider, {"last_test_status": status, "last_tested_on": now_datetime()},
							update_modified=False)
	return {"ok": ok, "status": status, "model": model, "latency_ms": int((time.monotonic() - started) * 1000)}


@frappe.whitelist(methods=["GET", "POST"])
def usage_summary() -> dict:
	"""Operational metadata only — no message content."""
	require_admin()
	month = get_first_day(nowdate())
	by_user = frappe.db.sql(
		f"""select user, count(*) requests, sum(input_tokens) input_tokens, sum(output_tokens) output_tokens,
		sum(estimated_cost) cost, sum(status='Error') errors, avg(latency_ms) avg_latency_ms
		from `tab{USAGE_LOG}` where creation >= %s group by user order by requests desc limit 50""",
		(month,), as_dict=True)
	by_model = frappe.db.sql(
		f"""select provider, model, count(*) requests, sum(input_tokens) input_tokens,
		sum(output_tokens) output_tokens, sum(estimated_cost) cost, sum(status='Error') errors
		from `tab{USAGE_LOG}` where creation >= %s group by provider, model""", (month,), as_dict=True)
	security = frappe.db.sql(
		f"""select event_type, severity, count(*) count from `tab{AUDIT_LOG}`
		where creation >= %s and severity in ('High','Critical','Warning') group by event_type, severity""",
		(month,), as_dict=True)
	return {"since": str(month), "by_user": by_user, "by_model": by_model, "security_events": security}
