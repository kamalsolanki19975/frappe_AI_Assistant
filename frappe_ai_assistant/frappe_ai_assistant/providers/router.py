"""Resolves a model slot (reasoning, fallback, fast, ...) to a configured adapter.

Credentials are read server-side with get_decrypted_password and never leave this module.
A simple Redis circuit breaker skips a provider that keeps failing and moves to the fallback slot.
"""

from __future__ import annotations

from urllib.parse import urlparse

import frappe
from frappe.utils import cint
from frappe.utils.password import get_decrypted_password

from frappe_ai_assistant.frappe_ai_assistant import PROVIDER

from .anthropic import AnthropicAdapter
from .base import ProviderConfig, ProviderError, ProviderNotConfigured
from .openai_compat import OpenAICompatAdapter
from .sarvam import SarvamAdapter

ADAPTERS = {
	"OpenAI": OpenAICompatAdapter,
	"Google Gemini": OpenAICompatAdapter,
	"OpenAI-Compatible": OpenAICompatAdapter,
	"Anthropic": AnthropicAdapter,
	"Sarvam": SarvamAdapter,
}
# Adapters tested in this release. Others are reachable only through OpenAI-compatible endpoints.
SLOTS = ("reasoning", "fallback", "fast", "vision", "embedding", "stt", "tts")

BREAKER_FAILURES = 5
BREAKER_WINDOW = 60
BREAKER_OPEN_SECONDS = 120


def validate_base_url(url: str | None):
	if not url:
		return
	parsed = urlparse(url)
	local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
	if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
		raise ProviderNotConfigured("Provider base URL must use https (http is allowed only for localhost).")
	if not parsed.hostname:
		raise ProviderNotConfigured("Provider base URL is not valid.")


def load_config(provider_name: str, model: str | None = None) -> ProviderConfig:
	if not provider_name:
		raise ProviderNotConfigured("No AI provider is selected in Chikku AI Settings.")
	row = frappe.db.get_value(
		PROVIDER, provider_name,
		["name", "provider_type", "enabled", "base_url", "default_model", "timeout_seconds", "max_retries",
		 "supports_tools", "supports_streaming"],
		as_dict=True,
	)
	if not row:
		raise ProviderNotConfigured("The selected AI provider no longer exists.")
	if not row.enabled:
		raise ProviderNotConfigured("The selected AI provider is disabled.")
	if row.provider_type not in ADAPTERS:
		raise ProviderNotConfigured("This provider type is not supported.")
	if row.provider_type == "OpenAI-Compatible" and not row.base_url:
		raise ProviderNotConfigured("OpenAI-Compatible providers need a Base URL.")
	validate_base_url(row.base_url)
	api_key = get_decrypted_password(PROVIDER, provider_name, "api_key", raise_exception=False)
	local = row.base_url and urlparse(row.base_url).hostname in ("localhost", "127.0.0.1", "::1")
	if not api_key and not local:
		raise ProviderNotConfigured("The AI provider has no API key configured.")
	return ProviderConfig(
		name=row.name, provider_type=row.provider_type, base_url=row.base_url or "", api_key=api_key or "",
		model=(model or row.default_model or "").strip(), timeout=cint(row.timeout_seconds) or 60,
		max_retries=cint(row.max_retries), supports_tools=bool(row.supports_tools),
		supports_streaming=bool(row.supports_streaming),
	)


def get_adapter(config: ProviderConfig):
	return ADAPTERS[config.provider_type](config)


def slot_config(settings, slot: str) -> ProviderConfig:
	if slot not in SLOTS:
		raise ProviderNotConfigured(f"Unknown model slot {slot}.")
	return load_config(settings.get(f"{slot}_provider"), settings.get(f"{slot}_model"))


def optional_slot(settings, slot: str, fallback: str | None = None):
	"""Config for an optional slot, falling back to another slot (e.g. vision -> reasoning). None if unavailable."""
	for name in (slot, fallback):
		if name and settings.get(f"{name}_provider"):
			try:
				return slot_config(settings, name)
			except ProviderNotConfigured:
				continue
	return None


def candidate_slots(settings) -> list[str]:
	slots = ["reasoning"]
	if settings.get("fallback_provider"):
		slots.append("fallback")
	return slots


# ---------------------------------------------------------------- circuit breaker
def _key(provider: str, part: str):
	return f"chikku_ai:breaker:{part}:{provider}"


def breaker_open(provider: str) -> bool:
	return bool(frappe.cache.get_value(_key(provider, "open")))


def record_failure(provider: str):
	key = frappe.cache.make_key(_key(provider, "fails"))
	pipe = frappe.cache.pipeline()
	pipe.incr(key)
	pipe.expire(key, BREAKER_WINDOW, nx=True)
	fails = pipe.execute()[0]
	if int(fails) >= BREAKER_FAILURES:
		frappe.cache.set_value(_key(provider, "open"), 1, expires_in_sec=BREAKER_OPEN_SECONDS)


def record_success(provider: str):
	frappe.cache.delete_value([_key(provider, "fails"), _key(provider, "open")])


__all__ = ["ProviderError", "get_adapter", "load_config", "slot_config", "candidate_slots"]
