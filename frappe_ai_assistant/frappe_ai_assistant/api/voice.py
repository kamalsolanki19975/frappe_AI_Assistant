"""Voice endpoints: consent, push-to-talk transcription, spoken replies."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr, now_datetime

from frappe_ai_assistant.frappe_ai_assistant import MESSAGE, USER_PREFERENCE
from frappe_ai_assistant.frappe_ai_assistant.security import limits
from frappe_ai_assistant.frappe_ai_assistant.security.access import get_owned_conversation, require_access
from frappe_ai_assistant.frappe_ai_assistant.voice import service


def _consented() -> bool:
	return bool(frappe.db.get_value(USER_PREFERENCE, frappe.session.user, "voice_consent"))


@frappe.whitelist(methods=["POST"])
def give_consent() -> dict:
	require_access("voice_enabled")
	user = frappe.session.user
	values = {"voice_consent": 1, "voice_consent_on": now_datetime()}
	if frappe.db.exists(USER_PREFERENCE, user):
		frappe.db.set_value(USER_PREFERENCE, user, values)
	else:
		frappe.get_doc({"doctype": USER_PREFERENCE, "user": user, **values}).insert(ignore_permissions=True)
	return {"consent": True}


@frappe.whitelist(methods=["POST"])
def transcribe() -> dict:
	settings = require_access("voice_enabled")
	if not service.status(settings)["input"]:
		frappe.throw(_("Voice input is not configured."))
	if not _consented():
		frappe.throw(_("Please accept the voice notice first."))
	limits.check_simple_rate("voice", 60, 3600)
	f = frappe.request.files.get("audio") if frappe.request and frappe.request.files else None
	if not f:
		frappe.throw(_("No audio received."))
	audio = f.stream.read()
	pref = frappe.db.get_value(USER_PREFERENCE, frappe.session.user, "preferred_language")
	rid = service.queue_transcription(audio, f.mimetype or f.content_type, pref)
	return {"request": rid}


@frappe.whitelist(methods=["POST"])
def speak(message: str) -> dict:
	settings = require_access("voice_enabled")
	if not service.status(settings)["output"]:
		frappe.throw(_("Spoken replies are not configured."))
	limits.check_simple_rate("voice_tts", 60, 3600)
	m = frappe.db.get_value(MESSAGE, cstr(message)[:140], ["name", "conversation", "role", "content", "language"], as_dict=True)
	if not m or m.role != "assistant":
		frappe.throw(_("Message not found."), frappe.DoesNotExistError)
	get_owned_conversation(m.conversation)
	text = service.speakable_text(m.content)
	if not text:
		frappe.throw(_("Nothing to read out."))
	return {"request": service.queue_speech(m.name, text, m.language)}
