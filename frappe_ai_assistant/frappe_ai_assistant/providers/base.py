"""Provider-neutral types and the adapter interface.

ERP code never imports a concrete adapter. The orchestrator talks to `router.get_client()`,
which returns something implementing `ProviderAdapter.chat`.

Neutral message format (list of dicts):
	{"role": "system"|"user"|"assistant"|"tool", "content": str,
	 "tool_calls": [ToolCall...] (assistant only), "tool_call_id": str (tool only), "name": str (tool only)}
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

from frappe_ai_assistant.frappe_ai_assistant.security.redaction import redact_text


class ProviderError(Exception):
	"""Base class. `category` is stable and safe to show/log; messages never contain secrets."""

	category = "provider_error"
	retryable = False

	def __init__(self, message: str = "", status_code: int | None = None):
		super().__init__(redact_text(message)[:500])
		self.status_code = status_code


class ProviderNotConfigured(ProviderError):
	category = "not_configured"


class ProviderAuthError(ProviderError):
	category = "auth_failed"


class ProviderRateLimited(ProviderError):
	category = "rate_limited"
	retryable = True


class ProviderUnavailable(ProviderError):
	category = "unavailable"
	retryable = True


class ProviderTimeout(ProviderError):
	category = "timeout"
	retryable = True


class ProviderBadResponse(ProviderError):
	category = "bad_response"


class ProviderBadRequest(ProviderError):
	category = "bad_request"


class TurnCancelled(Exception):
	pass


@dataclass
class ToolCall:
	id: str
	name: str
	arguments: dict


@dataclass
class ToolSpec:
	name: str
	description: str
	parameters: dict  # JSON schema


@dataclass
class ChatResult:
	text: str = ""
	tool_calls: list[ToolCall] = field(default_factory=list)
	input_tokens: int = 0
	output_tokens: int = 0
	stop_reason: str | None = None
	model: str | None = None


@dataclass
class ProviderConfig:
	name: str
	provider_type: str
	base_url: str
	api_key: str
	model: str
	timeout: int = 60
	max_retries: int = 2
	supports_tools: bool = True
	supports_streaming: bool = True

	def __repr__(self):  # never print the key
		return f"ProviderConfig(name={self.name!r}, type={self.provider_type!r}, model={self.model!r})"


DeltaCallback = Callable[[str], None]


class ProviderAdapter:
	def __init__(self, config: ProviderConfig, session: requests.Session | None = None):
		self.config = config
		self.session = session or requests.Session()

	def chat(self, messages: list[dict], tools: list[ToolSpec] | None = None, *, max_tokens: int = 1500,
			 temperature: float | None = 0.2, stream: bool = True,
			 on_delta: DeltaCallback | None = None) -> ChatResult:
		raise NotImplementedError

	# ---------------------------------------------------------------- shared HTTP helpers
	def _post(self, url: str, headers: dict, payload: dict, stream: bool) -> requests.Response:
		"""POST with retries for retryable failures. Retries happen only before any output is consumed."""
		attempts = max(0, int(self.config.max_retries)) + 1
		last: ProviderError | None = None
		for attempt in range(attempts):
			try:
				resp = self.session.post(
					url, headers=headers, data=json.dumps(payload), stream=stream,
					timeout=(10, self.config.timeout),
				)
			except requests.Timeout:
				last = ProviderTimeout("The AI provider did not respond in time.")
			except requests.ConnectionError:
				last = ProviderUnavailable("Could not connect to the AI provider.")
			else:
				if resp.status_code < 400:
					return resp
				last = self._error_for_status(resp)
				resp.close()
			if not last.retryable or attempt == attempts - 1:
				raise last
			time.sleep(min(8, 0.8 * (2**attempt)))
		raise last  # pragma: no cover

	@staticmethod
	def _error_for_status(resp: requests.Response) -> ProviderError:
		code = resp.status_code
		try:
			body = resp.text[:400]
		except Exception:
			body = ""
		msg = f"HTTP {code}: {body}"
		if code in (401, 403):
			return ProviderAuthError(msg, code)
		if code == 429:
			return ProviderRateLimited(msg, code)
		if code in (408, 504):
			return ProviderTimeout(msg, code)
		if code >= 500 or code == 529:
			return ProviderUnavailable(msg, code)
		return ProviderBadRequest(msg, code)

	@staticmethod
	def iter_sse(resp: requests.Response):
		"""Yield (event, data) pairs from a text/event-stream response."""
		event, data_lines = None, []
		for raw in resp.iter_lines(decode_unicode=True):
			if raw is None:
				continue
			line = raw.rstrip("\r")
			if not line:
				if data_lines:
					yield event, "\n".join(data_lines)
				event, data_lines = None, []
				continue
			if line.startswith(":"):
				continue
			if line.startswith("event:"):
				event = line[6:].strip()
			elif line.startswith("data:"):
				data_lines.append(line[5:].lstrip())
		if data_lines:
			yield event, "\n".join(data_lines)


def parse_arguments(raw) -> dict:
	if isinstance(raw, dict):
		return raw
	if not raw:
		return {}
	try:
		value = json.loads(raw)
	except (TypeError, ValueError):
		raise ProviderBadResponse("The AI provider returned malformed tool arguments.")
	if not isinstance(value, dict):
		raise ProviderBadResponse("The AI provider returned tool arguments that are not an object.")
	return value
