"""Per-user rate limits and token/cost budgets (Redis counters + usage log sums)."""

import frappe
from frappe import _
from frappe.utils import flt, get_first_day, nowdate

from frappe_ai_assistant.frappe_ai_assistant import USAGE_LOG


class LimitExceeded(frappe.ValidationError):
	http_status_code = 429


def _hit(bucket: str, limit: int, window_seconds: int, user: str) -> bool:
	"""Increment a fixed-window counter. Returns False when the limit is exceeded."""
	if not limit or limit <= 0:
		return True
	key = frappe.cache.make_key(f"chikku_ai:rl:{bucket}:{user}")
	pipe = frappe.cache.pipeline()
	pipe.incr(key)
	pipe.expire(key, window_seconds, nx=True)
	count = pipe.execute()[0]
	return int(count) <= int(limit)


def check_message_rate(settings, user: str | None = None):
	user = user or frappe.session.user
	if not _hit("min", settings.user_requests_per_minute, 60, user):
		_blocked("rate_limited_minute", user)
		frappe.throw(_("You're sending messages too quickly. Please wait a minute and try again."), LimitExceeded)
	if not _hit("day", settings.user_requests_per_day, 86400, user):
		_blocked("rate_limited_day", user)
		frappe.throw(_("You've reached today's message limit for Chikku AI."), LimitExceeded)


def check_simple_rate(bucket: str, limit: int, seconds: int, user: str | None = None):
	user = user or frappe.session.user
	if not _hit(bucket, limit, seconds, user):
		frappe.throw(_("Too many requests. Please try again shortly."), LimitExceeded)


def check_budgets(settings, user: str | None = None):
	user = user or frappe.session.user
	if settings.user_daily_token_limit:
		used = frappe.db.sql(
			f"""select coalesce(sum(input_tokens + output_tokens), 0) from `tab{USAGE_LOG}`
			where user=%s and creation >= %s""",
			(user, nowdate()),
		)[0][0]
		if flt(used) >= settings.user_daily_token_limit:
			_blocked("budget_user_daily_tokens", user)
			frappe.throw(_("You've used today's Chikku AI allowance. It resets tomorrow."), LimitExceeded)
	if flt(settings.monthly_cost_limit):
		spent = frappe.db.sql(
			f"select coalesce(sum(estimated_cost), 0) from `tab{USAGE_LOG}` where creation >= %s",
			(get_first_day(nowdate()),),
		)[0][0]
		if flt(spent) >= flt(settings.monthly_cost_limit):
			_blocked("budget_site_monthly_cost", user)
			frappe.throw(_("Chikku AI's monthly budget has been reached. Please contact your administrator."),
						 LimitExceeded)


def _blocked(reason: str, user: str):
	from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit

	audit("limit_blocked", severity="Warning", details={"reason": reason}, user=user)
