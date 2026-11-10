"""Anthropic Messages API adapter."""

from __future__ import annotations

import json

from .base import ChatResult, ProviderAdapter, ProviderBadResponse, ToolCall, ToolSpec, parse_arguments

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


class AnthropicAdapter(ProviderAdapter):
	def _url(self) -> str:
		return f"{(self.config.base_url or DEFAULT_BASE_URL).rstrip('/')}/messages"

	@staticmethod
	def to_wire(messages: list[dict]) -> tuple[str, list[dict]]:
		system_parts, out = [], []

		def push(role, block):
			if out and out[-1]["role"] == role:
				out[-1]["content"].append(block)
			else:
				out.append({"role": role, "content": [block]})

		for m in messages:
			role = m["role"]
			if role == "system":
				system_parts.append(m["content"])
			elif role == "tool":
				push("user", {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]})
			elif role == "assistant":
				if m.get("content"):
					push("assistant", {"type": "text", "text": m["content"]})
				for tc in m.get("tool_calls") or []:
					push("assistant", {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
			elif isinstance(m["content"], list):
				for part in m["content"]:
					if part.get("type") == "image":
						push("user", {"type": "image", "source": {"type": "base64", "media_type": part["media_type"],
																   "data": part["data"]}})
					else:
						push("user", {"type": "text", "text": part.get("text", "")})
			else:
				push("user", {"type": "text", "text": m["content"]})
		return "\n\n".join(system_parts), out

	def chat(self, messages, tools: list[ToolSpec] | None = None, *, max_tokens=1500, temperature=0.2,
			 stream=True, on_delta=None) -> ChatResult:
		stream = bool(stream and self.config.supports_streaming)
		system, wire = self.to_wire(messages)
		payload = {"model": self.config.model, "max_tokens": int(max_tokens), "messages": wire, "stream": stream}
		if system:
			payload["system"] = system
		if temperature is not None:
			payload["temperature"] = float(temperature)
		if tools and self.config.supports_tools:
			payload["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
								for t in tools]
		headers = {
			"Content-Type": "application/json",
			"x-api-key": self.config.api_key,
			"anthropic-version": API_VERSION,
		}
		resp = self._post(self._url(), headers, payload, stream)
		try:
			return self._read_stream(resp, on_delta) if stream else self._read_json(resp)
		finally:
			resp.close()

	def _read_json(self, resp) -> ChatResult:
		try:
			data = resp.json()
			blocks = data["content"]
		except (ValueError, KeyError, TypeError):
			raise ProviderBadResponse("Unexpected response from the AI provider.")
		usage = data.get("usage") or {}
		text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
		calls = [ToolCall(id=b["id"], name=b["name"], arguments=parse_arguments(b.get("input")))
				 for b in blocks if b.get("type") == "tool_use"]
		return ChatResult(text=text, tool_calls=calls, input_tokens=int(usage.get("input_tokens") or 0),
						  output_tokens=int(usage.get("output_tokens") or 0),
						  stop_reason=data.get("stop_reason"), model=data.get("model"))

	def _read_stream(self, resp, on_delta) -> ChatResult:
		result = ChatResult()
		parts: list[str] = []
		blocks: dict[int, dict] = {}
		for event, data in self.iter_sse(resp):
			try:
				evt = json.loads(data)
			except ValueError:
				raise ProviderBadResponse("Malformed streaming data from the AI provider.")
			etype = evt.get("type") or event
			if etype == "error":
				raise ProviderBadResponse(str(evt.get("error"))[:300])
			if etype == "message_start":
				msg = evt.get("message") or {}
				result.model = msg.get("model")
				result.input_tokens = int((msg.get("usage") or {}).get("input_tokens") or 0)
			elif etype == "content_block_start":
				block = evt.get("content_block") or {}
				blocks[int(evt.get("index", 0))] = {"type": block.get("type"), "id": block.get("id"),
													"name": block.get("name"), "json": ""}
			elif etype == "content_block_delta":
				delta = evt.get("delta") or {}
				if delta.get("type") == "text_delta":
					parts.append(delta.get("text", ""))
					if on_delta:
						on_delta(delta.get("text", ""))
				elif delta.get("type") == "input_json_delta":
					blocks.setdefault(int(evt.get("index", 0)), {"type": "tool_use", "json": ""})
					blocks[int(evt.get("index", 0))]["json"] += delta.get("partial_json", "")
			elif etype == "message_delta":
				result.stop_reason = (evt.get("delta") or {}).get("stop_reason") or result.stop_reason
				result.output_tokens = int((evt.get("usage") or {}).get("output_tokens") or result.output_tokens)
		result.text = "".join(parts)
		result.tool_calls = [
			ToolCall(id=b["id"], name=b["name"], arguments=parse_arguments(b["json"] or "{}"))
			for _i, b in sorted(blocks.items()) if b.get("type") == "tool_use"
		]
		return result
