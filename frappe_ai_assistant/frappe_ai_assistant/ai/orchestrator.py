"""One conversation turn: context -> prompt -> model -> (tools -> model)* -> answer.

Runs inside an RQ job as the user who sent the message. Does not commit; the job wrapper does.
"""

from __future__ import annotations

import json
import time

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime

from frappe_ai_assistant.frappe_ai_assistant import CONVERSATION, MESSAGE, REALTIME_EVENT, USER_PREFERENCE
from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit, log_usage
from frappe_ai_assistant.frappe_ai_assistant.providers import router
from frappe_ai_assistant.frappe_ai_assistant.providers.base import ProviderError, ProviderNotConfigured, TurnCancelled
from frappe_ai_assistant.frappe_ai_assistant.security.untrusted import looks_like_injection, wrap
from frappe_ai_assistant.frappe_ai_assistant.tools.registry import ToolContext, available_tools, run_tool

from . import greeting, language as lang, render
from .prompts import build_system_prompt

STREAM_FLUSH_SECONDS = 0.15
MAX_TOOL_RESULT_CHARS = 12000

FRIENDLY_ERRORS = {
	"not_configured": {
		"en": "I'm not fully set up yet: no AI provider is configured. Please ask your administrator to open "
		"Chikku AI Settings and add a provider and API key.",
		"hi": "मैं अभी पूरी तरह सेट नहीं हूँ: कोई AI प्रोवाइडर कॉन्फ़िगर नहीं है। कृपया अपने एडमिनिस्ट्रेटर से Chikku AI Settings में प्रोवाइडर और API key जोड़ने को कहें।",
		"hinglish": "Main abhi poori tarah set up nahi hoon: koi AI provider configure nahi hai. Please apne "
		"administrator se Chikku AI Settings mein provider aur API key add karwa lijiye.",
	},
	"auth_failed": {"en": "The AI provider rejected our credentials. Please ask your administrator to check the API key."},
	"rate_limited": {"en": "The AI provider is busy right now. Please try again in a minute."},
	"unavailable": {"en": "The AI provider is temporarily unavailable. Please try again shortly."},
	"timeout": {"en": "The AI provider took too long to respond. Please try again."},
	"bad_response": {"en": "The AI provider sent a response I couldn't understand. Please try again."},
	"bad_request": {"en": "The AI provider couldn't process this request. If it keeps happening, please tell your administrator."},
	"cancelled": {"en": "Stopped."},
	"internal": {"en": "Something went wrong on my side. The error has been logged for your administrator."},
}


def friendly(category: str, code: str) -> str:
	options = FRIENDLY_ERRORS.get(category) or FRIENDLY_ERRORS["internal"]
	return options.get(code) or options["en"]


def cancel_key(message: str) -> str:
	return f"chikku_ai:cancel:{message}"


def publish(user: str, payload: dict):
	frappe.publish_realtime(REALTIME_EVENT, payload, user=user)


class Streamer:
	"""Batches token deltas into realtime events and checks for cancellation."""

	def __init__(self, user, conversation, message):
		self.user, self.conversation, self.message = user, conversation, message
		self.buffer, self.full, self.last = [], [], time.monotonic()

	def __call__(self, delta: str):
		self.buffer.append(delta)
		self.full.append(delta)
		if time.monotonic() - self.last >= STREAM_FLUSH_SECONDS:
			self.flush()

	def flush(self):
		if frappe.cache.get_value(cancel_key(self.message)):
			raise TurnCancelled()
		if self.buffer:
			publish(self.user, {"type": "delta", "conversation": self.conversation, "message": self.message,
								"text": "".join(self.buffer)})
			self.buffer = []
		self.last = time.monotonic()


def _history(conversation: str, before_seq: int, limit: int) -> list[dict]:
	rows = frappe.get_all(  # Chikku's own table; ownership already verified for this conversation
		MESSAGE,
		filters={"conversation": conversation, "seq": ["<", before_seq], "status": "Completed",
				 "role": ["in", ["user", "assistant"]]},
		fields=["role", "content"],
		order_by="seq desc",
		limit_page_length=limit,
	)
	return [{"role": r.role, "content": cstr(r.content)} for r in reversed(rows) if r.content]


def _trim(messages: list[dict], max_chars: int) -> list[dict]:
	"""Drop oldest history (never the system prompt or the current message) until under budget."""
	def size(ms):
		return sum(len(m.get("content") or "") for m in ms)

	while len(messages) > 2 and size(messages) > max_chars:
		messages.pop(1)
	return messages


def _context_summary(ctx: dict) -> str | None:
	if not ctx:
		return None
	if ctx.get("kind") == "form" and ctx.get("card"):
		return ("The user currently has this SaarthiERP document open (validated, permission-checked):\n"
				+ wrap("erp_context", ctx["card"])
				+ "\nWhen the user says 'this', 'this order', 'yeh' etc., they likely mean this document.")
	if ctx.get("doctype"):
		sel = f" Selected records: {', '.join(ctx['selected'][:20])}." if ctx.get("selected") else ""
		return f"The user is viewing the {ctx['doctype']} {ctx.get('kind', 'list')}.{sel}"
	return None


def _attachment_note(attachments) -> str:
	if not attachments:
		return ""
	lines = [f"- file id `{a.get('file')}`: {a.get('file_name')} ({a.get('kind')}, {a.get('size_kb')} KB)" for a in attachments]
	return ("\n\n[Files attached to this message. Use the file id with the tools: spreadsheets -> analyse_spreadsheet / "
			"modify_spreadsheet / start_import; invoices, PDFs, images, DOCX -> read_document.]\n" + "\n".join(lines))


def run_turn(message_name: str):
	msg = frappe.get_doc(MESSAGE, message_name)
	user = frappe.session.user
	conv = frappe.db.get_value(CONVERSATION, msg.conversation, ["name", "user"], as_dict=True)
	if not conv or conv.user != user or msg.owner != user:
		audit("job_identity_mismatch", "Critical", user=user, conversation=msg.conversation,
			  details={"message": message_name})
		frappe.db.set_value(MESSAGE, message_name, {"status": "Failed", "error_code": "IDENTITY"})
		return

	reply = _create_reply(msg)
	publish(user, {"type": "status", "conversation": conv.name, "message": reply.name, "status": "Processing",
				   "user_message": msg.name})

	settings = frappe.get_cached_doc("Chikku AI Settings")
	pref = frappe.db.get_value(USER_PREFERENCE, user, ["preferred_language", "response_style"], as_dict=True) or {}
	code = lang.effective_language(msg.content, pref.get("preferred_language"), settings)
	context = json.loads(msg.context_json) if msg.context_json else {}
	attachments = json.loads(msg.attachments_json) if msg.attachments_json else []

	tool_ctx = ToolContext(user=user, settings=settings, conversation=conv.name, message=reply.name,
						   context=context, language=code)
	tools = available_tools(settings)
	specs = [t.spec() for t in tools]
	context_summary = _context_summary(context) if settings.erp_context_enabled else None
	prior = frappe.db.get_value(CONVERSATION, conv.name, "analytics_context")
	if prior:
		context_summary = (context_summary or "") + (
			"\n\nThe previous business analysis in this conversation used these parameters (reuse and adjust them for "
			"follow-up questions such as 'and last month?' or 'by customer'):\n" + wrap("previous_analysis", prior))
	system = build_system_prompt(settings, language=code, user_first_name=greeting.first_name(user),
								 response_style=pref.get("response_style"), has_tools=bool(tools),
								 context_summary=context_summary, tool_names={t.name for t in tools})
	messages = [{"role": "system", "content": system},
				*_history(conv.name, msg.seq, cint(settings.context_message_limit) or 20),
				{"role": "user", "content": cstr(msg.content) + _attachment_note(attachments)}]
	messages = _trim(messages, cint(settings.max_context_chars) or 24000)

	started = time.monotonic()
	deadline = started + (cint(settings.timeout_seconds) or 120)
	streamer = Streamer(user, conv.name, reply.name)
	text, provider_used, model_used = "", None, None
	tin = tout = 0
	error_category = None

	try:
		configs = []
		for slot in router.candidate_slots(settings):
			try:
				configs.append((slot, router.slot_config(settings, slot)))
			except ProviderNotConfigured:
				if slot == "reasoning":
					raise
		max_calls = cint(settings.max_tool_calls_per_turn) or 8
		tool_calls_made = 0
		for _round in range(max_calls + 1):
			if time.monotonic() > deadline:
				text = text or friendly("timeout", code)
				error_category = "timeout"
				break
			result, provider_used, model_used = _call_model(configs, messages, specs if tool_calls_made < max_calls else None,
															settings, streamer, user, conv.name, reply.name)
			tin += result.input_tokens
			tout += result.output_tokens
			streamer.flush()
			if not result.tool_calls:
				text = result.text
				break
			messages.append({"role": "assistant", "content": result.text, "tool_calls": result.tool_calls})
			for call in result.tool_calls:
				tool_calls_made += 1
				publish(user, {"type": "tool", "conversation": conv.name, "message": reply.name, "tool": call.name})
				out = run_tool(call.name, call.arguments, tool_ctx)
				payload = json.dumps(out, default=str, ensure_ascii=False)
				if len(payload) > MAX_TOOL_RESULT_CHARS:
					payload = payload[:MAX_TOOL_RESULT_CHARS] + '…"(truncated)'
				if looks_like_injection(payload):
					audit("injection_flag", "Warning", user=user, conversation=conv.name,
						  details={"tool": call.name, "message": reply.name})
				messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
								 "content": wrap("tool_result", payload)})
		else:
			text = text or _("I couldn't finish within the tool-call limit. Please try a narrower question.")
		status = "Completed"
	except TurnCancelled:
		status, error_category = "Cancelled", "cancelled"
		partial = "".join(streamer.full).strip()
		text = f"{partial}\n\n_({friendly('cancelled', code)})_" if partial else friendly("cancelled", code)
	except ProviderError as e:
		status, error_category = "Failed", e.category
		text = friendly(e.category, code)
		if e.category != "not_configured":
			frappe.log_error(title=f"Chikku AI provider error: {e.category}", message=str(e))
	except Exception:
		status, error_category = "Failed", "internal"
		text = friendly("internal", code)
		frappe.log_error(title="Chikku AI turn failed")

	latency = int((time.monotonic() - started) * 1000)
	reply.update({
		"status": status,
		"content": text,
		"content_html": render.to_html(text),
		"blocks_json": json.dumps(tool_ctx.blocks, default=str) if tool_ctx.blocks else None,
		"language": code,
		"provider": provider_used,
		"model": model_used,
		"input_tokens": tin,
		"output_tokens": tout,
		"latency_ms": latency,
		"error_code": error_category,
	})
	reply.flags.ignore_permissions = True
	reply.save()
	frappe.db.set_value(MESSAGE, msg.name, {"status": "Completed", "language": code}, update_modified=False)
	_update_conversation(conv.name, msg, code, tin, tout)

	for t in tool_ctx.action_tokens:  # tokens go only to this user's browser
		publish(user, {"type": "action_token", "conversation": conv.name, "message": reply.name, **t})
	publish(user, {"type": "done", "conversation": conv.name, "message": reply.name,
				   "payload": message_payload(reply)})
	return reply.name


def _call_model(configs, messages, specs, settings, streamer, user, conversation, reply_name):
	last_error = None
	for idx, (slot, cfg) in enumerate(configs):
		if router.breaker_open(cfg.name) and idx < len(configs) - 1:
			continue
		adapter = router.get_adapter(cfg)
		t0 = time.monotonic()
		try:
			res = adapter.chat(messages, specs, max_tokens=cint(settings.max_response_tokens) or 1500,
							   temperature=settings.temperature, stream=bool(settings.streaming_enabled),
							   on_delta=streamer)
		except TurnCancelled:
			raise
		except ProviderError as e:
			last_error = e
			router.record_failure(cfg.name)
			log_usage(user=user, provider=cfg.name, model=cfg.model, slot=slot, status="Error",
					  error_category=e.category, latency_ms=int((time.monotonic() - t0) * 1000),
					  conversation=conversation, message=reply_name)
			if e.retryable and idx < len(configs) - 1 and not streamer.full:
				audit("provider_failover", "Warning", user=user, conversation=conversation,
					  details={"from": cfg.name, "category": e.category})
				continue
			raise
		router.record_success(cfg.name)
		log_usage(user=user, provider=cfg.name, model=res.model or cfg.model, slot=slot, status="Success",
				  input_tokens=res.input_tokens, output_tokens=res.output_tokens,
				  latency_ms=int((time.monotonic() - t0) * 1000), conversation=conversation, message=reply_name)
		return res, cfg.name, res.model or cfg.model
	raise last_error or ProviderNotConfigured("No AI provider available.")


def _create_reply(msg):
	existing = frappe.db.get_value(MESSAGE, {"conversation": msg.conversation, "idempotency_key": f"reply:{msg.name}"})
	if existing:
		return frappe.get_doc(MESSAGE, existing)
	reply = frappe.get_doc({
		"doctype": MESSAGE,
		"conversation": msg.conversation,
		"role": "assistant",
		"status": "Processing",
		"seq": cint(msg.seq) + 1,
		"idempotency_key": f"reply:{msg.name}",
	})
	reply.insert(ignore_permissions=True)  # owner = session user (the conversation owner)
	frappe.db.set_value(MESSAGE, msg.name, "status", "Processing", update_modified=False)
	return reply


def _update_conversation(conversation: str, user_msg, code: str, tin: int, tout: int):
	conv = frappe.get_doc(CONVERSATION, conversation)
	if not conv.title:
		conv.title = cstr(user_msg.content).strip().split("\n")[0][:80] or _("New conversation")
	conv.language = code
	conv.message_count = frappe.db.count(MESSAGE, {"conversation": conversation})
	conv.last_message_at = now_datetime()
	conv.total_input_tokens = cint(conv.total_input_tokens) + tin
	conv.total_output_tokens = cint(conv.total_output_tokens) + tout
	conv.estimated_cost = frappe.db.sql(
		"select coalesce(sum(estimated_cost),0) from `tabChikku AI Usage Log` where conversation=%s", conversation
	)[0][0]
	conv.flags.ignore_permissions = True
	conv.save()


def message_payload(m) -> dict:
	"""What the browser receives for one message. Never includes tokens or internal context."""
	blocks = json.loads(m.blocks_json) if m.blocks_json else []
	attachments = json.loads(m.attachments_json) if m.attachments_json else []
	return {
		"name": m.name,
		"role": m.role,
		"status": m.status,
		"content": m.content if m.role == "user" else None,
		"content_html": m.content_html if m.role != "user" else None,
		"blocks": blocks,
		"attachments": attachments,
		"language": m.language,
		"error_code": m.error_code,
		"seq": m.seq,
		"creation": str(m.creation),
		"feedback": m.feedback,
	}
