"""Sarvam AI speech adapter (STT + TTS only). Contract verified against docs.sarvam.ai (2026-11):
STT  POST {base}/speech-to-text   multipart: file, model, language_code ('unknown' = auto) -> {transcript, language_code}
TTS  POST {base}/text-to-speech   json: text, language_code, model, speaker -> {audios: [base64 wav]}
Auth header: api-subscription-key
"""

from __future__ import annotations

import base64

import requests

from .base import ProviderAdapter, ProviderBadRequest, ProviderBadResponse, ProviderTimeout, ProviderUnavailable

DEFAULT_BASE_URL = "https://api.sarvam.ai"
TTS_LANGS = {"en": "en-IN", "hi": "hi-IN", "hinglish": "hi-IN"}
TTS_MAX_CHARS = 2500


class SarvamAdapter(ProviderAdapter):
	def _base(self):
		return (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")

	def chat(self, *a, **kw):
		raise ProviderBadRequest("Sarvam is configured for speech only in Chikku AI.")

	def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> dict:
		data = {"language_code": language if language and "-" in language else "unknown"}
		if self.config.model:
			data["model"] = self.config.model
		ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a"}.get(
			mime.split(";")[0], "webm")
		try:
			resp = self.session.post(f"{self._base()}/speech-to-text",
									 headers={"api-subscription-key": self.config.api_key},
									 data=data, files={"file": (f"audio.{ext}", audio, mime.split(";")[0])},
									 timeout=(10, self.config.timeout))
		except requests.Timeout:
			raise ProviderTimeout("The speech service did not respond in time.")
		except requests.ConnectionError:
			raise ProviderUnavailable("Could not connect to the speech service.")
		if resp.status_code >= 400:
			raise self._error_for_status(resp)
		try:
			body = resp.json()
			return {"text": body.get("transcript") or "", "language": body.get("language_code")}
		except ValueError:
			raise ProviderBadResponse("Unexpected transcription response.")

	def synthesize(self, text: str, language: str | None = None, voice: str | None = None) -> tuple[bytes, str]:
		payload = {"text": text[:TTS_MAX_CHARS], "language_code": TTS_LANGS.get(language or "en", "en-IN")}
		if self.config.model:
			payload["model"] = self.config.model
		if voice:
			payload["speaker"] = voice
		resp = self._post(f"{self._base()}/text-to-speech",
						  {"api-subscription-key": self.config.api_key, "Content-Type": "application/json"}, payload, False)
		try:
			audios = resp.json().get("audios") or []
			return base64.b64decode("".join(audios)), "audio/wav"
		except (ValueError, TypeError):
			raise ProviderBadResponse("Unexpected speech synthesis response.")
		finally:
			resp.close()
