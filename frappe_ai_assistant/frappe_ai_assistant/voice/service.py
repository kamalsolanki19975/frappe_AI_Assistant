"""Voice: push-to-talk speech recognition and spoken replies through provider slots (stt / tts).

Privacy: raw audio is never written to disk or the database. Uploaded audio is held in Redis for at most
120 seconds until the background job transcribes it, then deleted. Synthesised speech is sent straight to
the user's browser over the realtime channel and not stored.
"""

from __future__ import annotations

import base64
import time
import uuid

import frappe
from frappe import _
from frappe.utils import cstr, strip_html

from frappe_ai_assistant.frappe_ai_assistant import REALTIME_EVENT
from frappe_ai_assistant.frappe_ai_assistant.ai import language as lang
from frappe_ai_assistant.frappe_ai_assistant.audit.logger import log_usage
from frappe_ai_assistant.frappe_ai_assistant.providers import router
from frappe_ai_assistant.frappe_ai_assistant.providers.base import ProviderError

AUDIO_TTL = 120
MAX_AUDIO_BYTES = 5 * 1024 * 1024
ALLOWED_MIME = ("audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4")
STT_LANG = {"English": "en-IN", "Hindi": "hi-IN", "Hinglish": "hi-IN"}


def status(settings) -> dict:
	stt = router.optional_slot(settings, "stt")
	tts = router.optional_slot(settings, "tts")
	enabled = bool(settings.voice_enabled)
	return {"input": bool(enabled and stt), "output": bool(enabled and tts),
			"reason": None if (enabled and stt) else ("Voice is turned off." if not enabled else
													  "No speech recognition provider is configured.")}


def _key(rid: str) -> str:
	return f"chikku_ai:audio:{frappe.session.user}:{rid}"


def queue_transcription(audio: bytes, mime: str, preferred_language: str | None) -> str:
	mime = (mime or "").split(";")[0].strip().lower()
	if mime not in ALLOWED_MIME:
		frappe.throw(_("Unsupported audio format."))
	if not audio or len(audio) > MAX_AUDIO_BYTES:
		frappe.throw(_("The recording is empty or too long."))
	rid = uuid.uuid4().hex
	frappe.cache.set_value(_key(rid), {"audio": audio, "mime": mime}, expires_in_sec=AUDIO_TTL)
	frappe.enqueue("frappe_ai_assistant.frappe_ai_assistant.voice.service.transcribe_job", queue="short", timeout=90,
				   rid=rid, language=STT_LANG.get(preferred_language or ""))
	return rid


def transcribe_job(rid: str, language: str | None = None):
	user = frappe.session.user
	payload = frappe.cache.get_value(_key(rid))
	frappe.cache.delete_value(_key(rid))  # audio is discarded immediately, success or failure
	if not payload:
		frappe.publish_realtime(REALTIME_EVENT, {"type": "transcript", "request": rid, "error": _("The recording expired.")}, user=user)
		return
	settings = frappe.get_cached_doc("Chikku AI Settings")
	cfg = router.optional_slot(settings, "stt")
	t0 = time.monotonic()
	try:
		if not cfg:
			raise ProviderError("not configured")
		res = router.get_adapter(cfg).transcribe(payload["audio"], payload["mime"], language)
		text = cstr(res.get("text")).strip()
		log_usage(user=user, provider=cfg.name, model=cfg.model, slot="stt", status="Success",
				  latency_ms=int((time.monotonic() - t0) * 1000))
		frappe.publish_realtime(REALTIME_EVENT, {"type": "transcript", "request": rid, "text": text,
												 "language": lang.detect(text), "provider_language": res.get("language")}, user=user)
	except ProviderError as e:
		if cfg:
			log_usage(user=user, provider=cfg.name, model=cfg.model, slot="stt", status="Error", error_category=e.category,
					  latency_ms=int((time.monotonic() - t0) * 1000))
		frappe.publish_realtime(REALTIME_EVENT, {"type": "transcript", "request": rid,
												 "error": _("Speech recognition failed ({0}). Please type instead.").format(e.category)}, user=user)
	frappe.db.commit()


def speakable_text(html_or_text: str) -> str:
	text = strip_html(cstr(html_or_text))
	text = text.replace("|", " ").replace("*", "").replace("#", "")
	return " ".join(text.split())[:2400]


def queue_speech(message: str, text: str, language: str | None) -> str:
	rid = uuid.uuid4().hex
	frappe.enqueue("frappe_ai_assistant.frappe_ai_assistant.voice.service.speak_job", queue="short", timeout=90, rid=rid,
				   message=message, text=text, language=language)
	return rid


def speak_job(rid: str, message: str, text: str, language: str | None = None):
	user = frappe.session.user
	settings = frappe.get_cached_doc("Chikku AI Settings")
	cfg = router.optional_slot(settings, "tts")
	t0 = time.monotonic()
	try:
		if not cfg:
			raise ProviderError("not configured")
		audio, mime = router.get_adapter(cfg).synthesize(text, language or "en", settings.tts_speaker or None)
		log_usage(user=user, provider=cfg.name, model=cfg.model, slot="tts", status="Success",
				  latency_ms=int((time.monotonic() - t0) * 1000))
		frappe.publish_realtime(REALTIME_EVENT, {"type": "speech", "request": rid, "message": message,
												 "audio": f"data:{mime};base64,{base64.b64encode(audio).decode()}"}, user=user)
	except ProviderError as e:
		frappe.publish_realtime(REALTIME_EVENT, {"type": "speech", "request": rid, "message": message,
												 "error": _("Speech playback failed ({0}).").format(e.category)}, user=user)
	frappe.db.commit()
