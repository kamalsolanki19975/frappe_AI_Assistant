"""Chat endpoints. Every endpoint: logged-in user, Chikku access, ownership checked server-side.
LLM calls never run here; send_message stores the message and enqueues the turn."""

from __future__ import annotations

import json
import uuid

import frappe
from frappe import _
from frappe.utils import cint, cstr

from frappe_ai_assistant.frappe_ai_assistant import CONVERSATION, MESSAGE, TURN_QUEUE, USER_PREFERENCE
from frappe_ai_assistant.frappe_ai_assistant.ai import greeting, language as lang
from frappe_ai_assistant.frappe_ai_assistant.ai.orchestrator import cancel_key, message_payload
from frappe_ai_assistant.frappe_ai_assistant.context.resolver import resolve
from frappe_ai_assistant.frappe_ai_assistant.providers import router
from frappe_ai_assistant.frappe_ai_assistant.providers.base import ProviderNotConfigured
from frappe_ai_assistant.frappe_ai_assistant.security import limits
from frappe_ai_assistant.frappe_ai_assistant.security.access import get_owned_conversation, require_access

MAX_TEXT = 8000
MAX_ATTACHMENTS = 8
LANG_CHOICES = ("Auto", "English", "Hindi", "Hinglish")


def _json_arg(value, default):
	if value in (None, ""):
		return default
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			frappe.throw(_("Invalid request."))
	return value


def _preference():
	user = frappe.session.user
	return frappe.db.get_value(USER_PREFERENCE, user, ["preferred_language", "greeting_enabled", "response_style"],
							   as_dict=True) or frappe._dict(preferred_language="Auto", greeting_enabled=1)


def _provider_ready(settings) -> bool:
	try:
		router.slot_config(settings, "reasoning")
		return True
	except ProviderNotConfigured:
		return False


def _greeting_language(settings, pref) -> str:
	code = lang.PREF_TO_CODE.get(pref.get("preferred_language") or "")
	if not code and settings.default_language not in (None, "", "Auto"):
		code = lang.PREF_TO_CODE.get(settings.default_language)
	if not code:
		last = frappe.db.get_value(CONVERSATION, {"user": frappe.session.user, "language": ["is", "set"]},
								   "language", order_by="last_message_at desc")
		code = last if last in lang.LANGS else "en"
	if code == "hi" and not settings.hindi_enabled or code == "hinglish" and not settings.hinglish_enabled:
		code = "en"
	return code


def _conversation_rows(start=0, page_length=20):
	return frappe.get_list(
		CONVERSATION,
		filters={"user": frappe.session.user, "status": "Active", "message_count": [">", 0]},
		fields=["name", "title", "last_message_at", "message_count", "language"],
		order_by="last_message_at desc",
		limit_start=cint(start),
		limit_page_length=min(cint(page_length) or 20, 50),
	)


def _capabilities(settings) -> dict:
	from frappe_ai_assistant.frappe_ai_assistant.security.access import capability_allowed, is_admin
	from frappe_ai_assistant.frappe_ai_assistant.voice.service import status as voice_status

	on = lambda f: bool(settings.get(f)) and capability_allowed(settings, f)  # noqa: E731
	voice = voice_status(settings) if on("voice_enabled") else {"input": False, "output": False, "reason": "Voice is turned off."}
	return {
		"attachments": on("attachments_enabled"), "context": on("erp_context_enabled"), "drafts": on("draft_actions_enabled"),
		"excel": on("excel_enabled"), "import": on("import_enabled"), "analytics": on("analytics_enabled"),
		"documents": on("document_intelligence_enabled"), "knowledge": on("knowledge_enabled"),
		"workflows": on("workflow_intelligence_enabled") and is_admin(), "alerts": on("notifications_enabled"),
		"voice_input": voice["input"], "voice_output": voice["output"], "voice_reason": voice["reason"],
		"voice_consent": bool(frappe.db.get_value(USER_PREFERENCE, frappe.session.user, "voice_consent")),
		"voice_max_seconds": cint(settings.voice_max_seconds) or 30,
		"max_upload_mb": cint(settings.max_upload_mb) or 10, "is_admin": is_admin(),
		"new_alerts": frappe.db.count("Chikku AI Alert", {"user": frappe.session.user, "status": "New"}),
	}


@frappe.whitelist(methods=["POST"])
def open_session(conversation: str | None = None) -> dict:
	"""Called when the panel opens. Returns UI config, history list and (for an empty chat) a greeting."""
	settings = require_access("chat_enabled")
	pref = _preference()
	messages, conv_name = [], None
	if conversation:
		get_owned_conversation(conversation)
		conv_name = conversation
		messages = _messages(conversation, None, 30)
	code = _greeting_language(settings, pref)
	greet = None
	if not messages and settings.greeting_enabled and cint(pref.get("greeting_enabled", 1)):
		greet = greeting.build(settings, code)
	return {
		"assistant": {
			"name": settings.assistant_name or "Chikku AI",
			"avatar": settings.avatar,
			"description": settings.description,
		},
		"provider_ready": _provider_ready(settings),
		"capabilities": _capabilities(settings),
		"language": pref.get("preferred_language") or "Auto",
		"greeting_language": code,
		"greeting": greet,
		"suggested_prompts": [
			{"prompt": p.prompt, "language": p.language, "doctype": p.reference_doctype}
			for p in settings.suggested_prompts or []
		],
		"conversations": _conversation_rows(),
		"conversation": conv_name,
		"messages": messages,
	}


@frappe.whitelist(methods=["GET", "POST"])
def list_conversations(start: int = 0, page_length: int = 20) -> list:
	require_access("chat_enabled")
	return _conversation_rows(start, page_length)


def _messages(conversation: str, before_seq: int | None, limit: int) -> list:
	filters = {"conversation": conversation}
	if before_seq:
		filters["seq"] = ["<", cint(before_seq)]
	names = frappe.get_list(MESSAGE, filters=filters, pluck="name", order_by="seq desc",
							limit_page_length=min(cint(limit) or 30, 100))
	return [message_payload(frappe.get_doc(MESSAGE, n)) for n in reversed(names)]


@frappe.whitelist(methods=["GET", "POST"])
def get_messages(conversation: str, before_seq: int | None = None, limit: int = 30) -> dict:
	require_access("chat_enabled")
	get_owned_conversation(conversation)
	msgs = _messages(conversation, before_seq, limit)
	return {"conversation": conversation, "messages": msgs, "has_more": bool(msgs) and cint(msgs[0]["seq"]) > 1}


def _new_conversation() -> str:
	doc = frappe.get_doc({"doctype": CONVERSATION, "user": frappe.session.user, "status": "Active"})
	doc.insert()
	return doc.name


@frappe.whitelist(methods=["POST"])
def create_conversation() -> dict:
	require_access("chat_enabled")
	return {"conversation": _new_conversation()}


def _attachments(conversation: str, names) -> list:
	names = _json_arg(names, [])
	if not isinstance(names, list) or len(names) > MAX_ATTACHMENTS:
		frappe.throw(_("Too many attachments."))
	out = []
	for name in names:
		f = frappe.db.get_value("File", cstr(name)[:140],
								["name", "file_name", "file_size", "owner", "attached_to_doctype", "attached_to_name",
								 "is_private"], as_dict=True)
		if not f or f.owner != frappe.session.user or f.attached_to_doctype != CONVERSATION \
				or f.attached_to_name != conversation or not f.is_private:
			frappe.throw(_("Attachment not found."), frappe.DoesNotExistError)
		ext = (f.file_name or "").rsplit(".", 1)[-1].lower()
		out.append({"file": f.name, "file_name": f.file_name, "kind": ext,
					"size_kb": round((f.file_size or 0) / 1024, 1)})
	return out


@frappe.whitelist(methods=["POST"])
def send_message(text: str, conversation: str | None = None, context: str | dict | None = None,
				 attachments: str | list | None = None, idempotency_key: str | None = None) -> dict:
	settings = require_access("chat_enabled")
	text = cstr(text).replace("\x00", "").strip()
	if not text:
		frappe.throw(_("Please type a message."))
	if len(text) > MAX_TEXT:
		frappe.throw(_("Your message is too long (maximum {0} characters).").format(MAX_TEXT))
	idempotency_key = cstr(idempotency_key)[:64] or None

	if conversation:
		get_owned_conversation(conversation, for_update=True)
	else:
		conversation = _new_conversation()

	if idempotency_key:  # a retried send returns the original message instead of a duplicate
		existing = frappe.db.get_value(MESSAGE, {"conversation": conversation, "idempotency_key": idempotency_key})
		if existing:
			return {"conversation": conversation, "message": message_payload(frappe.get_doc(MESSAGE, existing)),
					"duplicate": True}

	limits.check_message_rate(settings)
	limits.check_budgets(settings)
	busy_since = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-(cint(settings.timeout_seconds) or 120) - 120)
	if frappe.db.exists(MESSAGE, {"conversation": conversation, "status": ["in", ["Queued", "Processing"]],
								  "modified": [">", busy_since]}):
		frappe.throw(_("Please wait for the current reply to finish."))

	ctx = resolve(_json_arg(context, None)) if settings.erp_context_enabled else {}
	files = _attachments(conversation, attachments) if attachments else []
	if files and not settings.attachments_enabled:
		frappe.throw(_("Attachments are turned off by your administrator."))

	seq = cint(frappe.db.sql(f"select max(seq) from `tab{MESSAGE}` where conversation=%s", conversation)[0][0]) + 1
	msg = frappe.get_doc({
		"doctype": MESSAGE,
		"conversation": conversation,
		"role": "user",
		"status": "Queued",
		"seq": seq,
		"content": text,
		"language": lang.detect(text),
		"modality": "text",
		"context_json": json.dumps(ctx, default=str) if ctx else None,
		"attachments_json": json.dumps(files) if files else None,
		"idempotency_key": idempotency_key or uuid.uuid4().hex,
	})
	msg.insert(ignore_permissions=True)  # Message is code-written; ownership verified above
	job_id = f"chikku_ai_turn::{msg.name}"
	frappe.db.set_value(MESSAGE, msg.name, "job_id", job_id, update_modified=False)
	frappe.enqueue(
		"frappe_ai_assistant.frappe_ai_assistant.jobs.run_turn_job",
		queue=TURN_QUEUE,
		timeout=(cint(settings.timeout_seconds) or 120) + 60,
		job_id=job_id,
		deduplicate=True,
		enqueue_after_commit=True,
		message=msg.name,
	)
	msg.reload()
	return {"conversation": conversation, "message": message_payload(msg)}


@frappe.whitelist(methods=["POST"])
def cancel_turn(message: str) -> dict:
	require_access("chat_enabled")
	m = frappe.db.get_value(MESSAGE, cstr(message)[:140], ["name", "conversation"], as_dict=True)
	if not m:
		frappe.throw(_("Message not found."), frappe.DoesNotExistError)
	get_owned_conversation(m.conversation)
	frappe.cache.set_value(cancel_key(m.name), 1, expires_in_sec=600)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def rename_conversation(conversation: str, title: str) -> dict:
	require_access("chat_enabled")
	get_owned_conversation(conversation)
	frappe.db.set_value(CONVERSATION, conversation, "title", cstr(title).strip()[:140] or None)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def archive_conversation(conversation: str) -> dict:
	require_access("chat_enabled")
	get_owned_conversation(conversation)
	frappe.db.set_value(CONVERSATION, conversation, "status", "Archived")
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def set_language(language: str) -> dict:
	require_access("chat_enabled")
	if language not in LANG_CHOICES:
		frappe.throw(_("Unsupported language."))
	user = frappe.session.user
	if frappe.db.exists(USER_PREFERENCE, user):
		frappe.db.set_value(USER_PREFERENCE, user, "preferred_language", language)
	else:
		frappe.get_doc({"doctype": USER_PREFERENCE, "user": user, "preferred_language": language}).insert(
			ignore_permissions=True)
	return {"language": language}


@frappe.whitelist(methods=["POST"])
def set_feedback(message: str, feedback: str) -> dict:
	require_access("chat_enabled")
	if feedback not in ("", "Helpful", "Not Helpful"):
		frappe.throw(_("Invalid feedback."))
	m = frappe.db.get_value(MESSAGE, cstr(message)[:140], ["name", "conversation", "role"], as_dict=True)
	if not m or m.role != "assistant":
		frappe.throw(_("Message not found."), frappe.DoesNotExistError)
	get_owned_conversation(m.conversation)
	frappe.db.set_value(MESSAGE, m.name, "feedback", feedback or None, update_modified=False)
	return {"ok": True}
