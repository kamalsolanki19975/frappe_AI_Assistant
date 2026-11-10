"""OpenAI Chat Completions protocol: OpenAI, Google Gemini (OpenAI-compatible endpoint),
and any OpenAI-compatible server (Azure-style gateways, Groq, vLLM, Ollama ...)."""

from __future__ import annotations

import json

from .base import (
	ChatResult,
	ProviderAdapter,
	ProviderBadResponse,
	ProviderTimeout,
	ProviderUnavailable,
	ToolCall,
	ToolSpec,
	TurnCancelled,
	parse_arguments,
)

DEFAULT_BASE_URLS = {
	"OpenAI": "https://api.openai.com/v1",
	"Google Gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}


class OpenAICompatAdapter(ProviderAdapter):
	def _url(self) -> str:
		base = (self.config.base_url or DEFAULT_BASE_URLS.get(self.config.provider_type) or "").rstrip("/")
		return f"{base}/chat/completions"

	@staticmethod
	def to_wire(messages: list[dict]) -> list[dict]:
		out = []
		for m in messages:
			role = m["role"]
			if role == "assistant" and m.get("tool_calls"):
				out.append({
					"role": "assistant",
					"content": m.get("content") or None,
					"tool_calls": [
						{"id": tc.id, "type": "function",
						 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
						for tc in m["tool_calls"]
					],
				})
			elif role == "tool":
				out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
			else:
				out.append({"role": role, "content": OpenAICompatAdapter._content(m["content"])})
		return out

	@staticmethod
	def _content(content):
		"""Neutral content (str or list of text/image parts) -> OpenAI format."""
		if isinstance(content, str):
			return content
		parts = []
		for p in content:
			if p.get("type") == "image":
				parts.append({"type": "image_url", "image_url": {"url": f"data:{p['media_type']};base64,{p['data']}"}})
			else:
				parts.append({"type": "text", "text": p.get("text", "")})
		return parts

	# ---------------------------------------------------------------- embeddings / speech
	def _base(self) -> str:
		return (self.config.base_url or DEFAULT_BASE_URLS.get(self.config.provider_type) or "").rstrip("/")

	def _auth(self) -> dict:
		return {"Authorization": f"Bearer {self.config.api_key}"}

	def embed(self, texts: list[str]) -> list[list[float]]:
		resp = self._post(f"{self._base()}/embeddings", {**self._auth(), "Content-Type": "application/json"},
						  {"model": self.config.model, "input": texts}, False)
		try:
			data = resp.json()["data"]
			return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]
		except (ValueError, KeyError, TypeError):
			raise ProviderBadResponse("Unexpected embeddings response.")
		finally:
			resp.close()

	def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> dict:
		import requests

		ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a"}.get(
			mime.split(";")[0], "webm")
		data = {"model": self.config.model}
		if language:
			data["language"] = language.split("-")[0]
		try:
			resp = self.session.post(f"{self._base()}/audio/transcriptions", headers=self._auth(), data=data,
									 files={"file": (f"audio.{ext}", audio, mime.split(";")[0])},
									 timeout=(10, self.config.timeout))
		except requests.Timeout:
			raise ProviderTimeout("The speech service did not respond in time.")
		except requests.ConnectionError:
			raise ProviderUnavailable("Could not connect to the speech service.")
		if resp.status_code >= 400:
			raise self._error_for_status(resp)
		try:
			body = resp.json()
			return {"text": body.get("text", ""), "language": body.get("language")}
		except ValueError:
			raise ProviderBadResponse("Unexpected transcription response.")

	def synthesize(self, text: str, language: str | None = None, voice: str | None = None) -> tuple[bytes, str]:
		resp = self._post(f"{self._base()}/audio/speech", {**self._auth(), "Content-Type": "application/json"},
						  {"model": self.config.model, "input": text[:4000], "voice": voice or "alloy",
						   "response_format": "mp3"}, False)
		try:
			return resp.content, "audio/mpeg"
		finally:
			resp.close()

	def chat(self, messages, tools: list[ToolSpec] | None = None, *, max_tokens=1500, temperature=0.2,
			 stream=True, on_delta=None) -> ChatResult:
		stream = bool(stream and self.config.supports_streaming)
		payload = {
			"model": self.config.model,
			"messages": self.to_wire(messages),
			"max_tokens": int(max_tokens),
			"stream": stream,
		}
		if temperature is not None:
			payload["temperature"] = float(temperature)
		if tools and self.config.supports_tools:
			payload["tools"] = [
				{"type": "function", "function": {"name": t.name, "description": t.description,
												  "parameters": t.parameters}}
				for t in tools
			]
		if stream:
			payload["stream_options"] = {"include_usage": True}
		headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key}"}
		resp = self._post(self._url(), headers, payload, stream)
		try:
			return self._read_stream(resp, on_delta) if stream else self._read_json(resp)
		finally:
			resp.close()

	def _read_json(self, resp) -> ChatResult:
		try:
			data = resp.json()
			choice = data["choices"][0]
			msg = choice["message"]
		except (ValueError, KeyError, IndexError, TypeError):
			raise ProviderBadResponse("Unexpected response from the AI provider.")
		usage = data.get("usage") or {}
		calls = [
			ToolCall(id=tc.get("id") or f"call_{i}", name=tc["function"]["name"],
					 arguments=parse_arguments(tc["function"].get("arguments")))
			for i, tc in enumerate(msg.get("tool_calls") or [])
		]
		return ChatResult(text=msg.get("content") or "", tool_calls=calls,
						  input_tokens=int(usage.get("prompt_tokens") or 0),
						  output_tokens=int(usage.get("completion_tokens") or 0),
						  stop_reason=choice.get("finish_reason"), model=data.get("model"))

	def _read_stream(self, resp, on_delta) -> ChatResult:
		result = ChatResult()
		parts: list[str] = []
		pending: dict[int, dict] = {}
		for _event, data in self.iter_sse(resp):
			if data.strip() == "[DONE]":
				break
			try:
				chunk = json.loads(data)
			except ValueError:
				raise ProviderBadResponse("Malformed streaming data from the AI provider.")
			if chunk.get("error"):
				raise ProviderBadResponse(str(chunk["error"])[:300])
			if chunk.get("usage"):
				result.input_tokens = int(chunk["usage"].get("prompt_tokens") or 0)
				result.output_tokens = int(chunk["usage"].get("completion_tokens") or 0)
			result.model = chunk.get("model") or result.model
			for choice in chunk.get("choices") or []:
				delta = choice.get("delta") or {}
				if delta.get("content"):
					parts.append(delta["content"])
					if on_delta:
						on_delta(delta["content"])
				for tc in delta.get("tool_calls") or []:
					slot = pending.setdefault(int(tc.get("index", 0)), {"id": None, "name": "", "args": ""})
					slot["id"] = tc.get("id") or slot["id"]
					fn = tc.get("function") or {}
					slot["name"] += fn.get("name") or ""
					slot["args"] += fn.get("arguments") or ""
				if choice.get("finish_reason"):
					result.stop_reason = choice["finish_reason"]
		result.text = "".join(parts)
		result.tool_calls = [
			ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments=parse_arguments(s["args"]))
			for i, s in sorted(pending.items())
		]
		return result


__all__ = ["OpenAICompatAdapter", "TurnCancelled"]
