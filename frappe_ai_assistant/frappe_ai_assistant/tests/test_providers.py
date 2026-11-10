"""Provider adapters tested over real HTTP against a local scripted server (no external calls)."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import frappe

from frappe_ai_assistant.frappe_ai_assistant.providers import router
from frappe_ai_assistant.frappe_ai_assistant.providers.anthropic import AnthropicAdapter
from frappe_ai_assistant.frappe_ai_assistant.providers.base import (
	ProviderAuthError,
	ProviderBadResponse,
	ProviderConfig,
	ProviderNotConfigured,
	ProviderRateLimited,
	ProviderTimeout,
	ProviderUnavailable,
	ToolSpec,
)
from frappe_ai_assistant.frappe_ai_assistant.providers.openai_compat import OpenAICompatAdapter

SECRET = "sk-test-SECRET-0123456789abcdef"


class Script:
	responses: list = []
	requests: list = []


class Handler(BaseHTTPRequestHandler):
	def log_message(self, *a):
		pass

	def do_POST(self):
		body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
		try:
			parsed = json.loads(body or b"{}")
		except ValueError:
			parsed = {"_raw": body[:200]}
		Script.requests.append({"path": self.path, "headers": dict(self.headers), "json": parsed})
		status, ctype, payload, delay = Script.responses.pop(0)
		if delay:
			import time

			time.sleep(delay)
		self.send_response(status)
		self.send_header("Content-Type", ctype)
		self.end_headers()
		self.wfile.write(payload.encode())


def sse(events):
	out = []
	for e in events:
		if isinstance(e, tuple):
			out.append(f"event: {e[0]}\ndata: {json.dumps(e[1])}\n\n")
		else:
			out.append(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n")
	return "".join(out)


class ProviderHTTPTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		cls.port = cls.server.server_address[1]
		cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
		cls.thread.start()

	@classmethod
	def tearDownClass(cls):
		cls.server.shutdown()

	def setUp(self):
		Script.responses, Script.requests = [], []

	def cfg(self, ptype="OpenAI-Compatible", **kw):
		return ProviderConfig(name="t", provider_type=ptype, base_url=f"http://127.0.0.1:{self.port}/v1",
							  api_key=SECRET, model="m1", timeout=kw.get("timeout", 5),
							  max_retries=kw.get("retries", 2))

	# ---------------------------------------------------------------- OpenAI protocol
	def test_openai_stream_text_and_usage(self):
		Script.responses = [(200, "text/event-stream", sse([
			{"choices": [{"delta": {"content": "Hello "}}], "model": "m1"},
			{"choices": [{"delta": {"content": "Kamal"}, "finish_reason": "stop"}]},
			{"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
			"[DONE]",
		]), 0)]
		deltas = []
		res = OpenAICompatAdapter(self.cfg()).chat([{"role": "user", "content": "hi"}], on_delta=deltas.append)
		self.assertEqual(res.text, "Hello Kamal")
		self.assertEqual("".join(deltas), "Hello Kamal")
		self.assertEqual((res.input_tokens, res.output_tokens), (12, 3))
		req = Script.requests[0]
		self.assertEqual(req["path"], "/v1/chat/completions")
		self.assertEqual(req["headers"]["Authorization"], f"Bearer {SECRET}")
		self.assertTrue(req["json"]["stream"])

	def test_openai_stream_tool_call_assembled_from_fragments(self):
		Script.responses = [(200, "text/event-stream", sse([
			{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "search_items", "arguments": "{\"search_"}}]}}]},
			{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "text\": \"cotton\"}"}}]}, "finish_reason": "tool_calls"}]},
			"[DONE]",
		]), 0)]
		spec = ToolSpec("search_items", "d", {"type": "object", "properties": {}})
		res = OpenAICompatAdapter(self.cfg()).chat([{"role": "user", "content": "x"}], [spec])
		self.assertEqual(len(res.tool_calls), 1)
		self.assertEqual(res.tool_calls[0].name, "search_items")
		self.assertEqual(res.tool_calls[0].arguments, {"search_text": "cotton"})
		self.assertEqual(Script.requests[0]["json"]["tools"][0]["function"]["name"], "search_items")

	def test_openai_non_stream(self):
		Script.responses = [(200, "application/json", json.dumps({
			"model": "m1", "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
			"usage": {"prompt_tokens": 4, "completion_tokens": 1}}), 0)]
		res = OpenAICompatAdapter(self.cfg()).chat([{"role": "user", "content": "x"}], stream=False)
		self.assertEqual(res.text, "OK")

	def test_retry_on_429_then_success(self):
		Script.responses = [
			(429, "application/json", '{"error":"slow down"}', 0),
			(200, "application/json", json.dumps({"choices": [{"message": {"content": "done"}}]}), 0),
		]
		res = OpenAICompatAdapter(self.cfg(retries=1)).chat([{"role": "user", "content": "x"}], stream=False)
		self.assertEqual(res.text, "done")
		self.assertEqual(len(Script.requests), 2)

	def test_rate_limit_exhausted(self):
		Script.responses = [(429, "application/json", "{}", 0)] * 2
		with self.assertRaises(ProviderRateLimited):
			OpenAICompatAdapter(self.cfg(retries=1)).chat([{"role": "user", "content": "x"}], stream=False)

	def test_auth_error_not_retried_and_secret_not_in_message(self):
		Script.responses = [(401, "application/json", json.dumps({"error": f"bad key {SECRET}"}), 0)]
		with self.assertRaises(ProviderAuthError) as cm:
			OpenAICompatAdapter(self.cfg()).chat([{"role": "user", "content": "x"}], stream=False)
		self.assertEqual(len(Script.requests), 1)
		self.assertNotIn(SECRET, str(cm.exception))

	def test_server_error_is_unavailable(self):
		Script.responses = [(503, "text/plain", "down", 0)]
		with self.assertRaises(ProviderUnavailable):
			OpenAICompatAdapter(self.cfg(retries=0)).chat([{"role": "user", "content": "x"}], stream=False)

	def test_timeout(self):
		Script.responses = [(200, "application/json", "{}", 2.5)]
		with self.assertRaises(ProviderTimeout):
			OpenAICompatAdapter(self.cfg(timeout=1, retries=0)).chat([{"role": "user", "content": "x"}], stream=False)

	def test_malformed_response(self):
		Script.responses = [(200, "application/json", "not json", 0)]
		with self.assertRaises(ProviderBadResponse):
			OpenAICompatAdapter(self.cfg()).chat([{"role": "user", "content": "x"}], stream=False)

	def test_malformed_tool_arguments(self):
		Script.responses = [(200, "application/json", json.dumps({"choices": [{"message": {"tool_calls": [
			{"id": "1", "function": {"name": "x", "arguments": "{not json"}}]}}]}), 0)]
		with self.assertRaises(ProviderBadResponse):
			OpenAICompatAdapter(self.cfg()).chat([{"role": "user", "content": "x"}], stream=False)

	# ---------------------------------------------------------------- Anthropic protocol
	def test_anthropic_stream_text_and_tool(self):
		Script.responses = [(200, "text/event-stream", sse([
			("message_start", {"type": "message_start", "message": {"model": "claude-x", "usage": {"input_tokens": 20}}}),
			("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
			("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking"}}),
			("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu1", "name": "get_item_stock"}}),
			("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"item_code\": "}}),
			("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"ABC\"}"}}),
			("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}}),
			("message_stop", {"type": "message_stop"}),
		]), 0)]
		cfg = self.cfg("Anthropic")
		res = AnthropicAdapter(cfg).chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "x"}],
										 [ToolSpec("get_item_stock", "d", {"type": "object", "properties": {}})])
		self.assertEqual(res.text, "Checking")
		self.assertEqual(res.tool_calls[0].arguments, {"item_code": "ABC"})
		self.assertEqual((res.input_tokens, res.output_tokens), (20, 9))
		req = Script.requests[0]
		self.assertEqual(req["path"], "/v1/messages")
		self.assertEqual(req["headers"]["x-api-key"], SECRET)
		self.assertEqual(req["json"]["system"], "sys")
		self.assertEqual(req["json"]["tools"][0]["input_schema"]["type"], "object")

	def test_sarvam_speech_to_text_and_tts(self):
		import base64

		from frappe_ai_assistant.frappe_ai_assistant.providers.sarvam import SarvamAdapter

		Script.responses = [(200, "application/json", json.dumps({"transcript": "aaj ki sales", "language_code": "hi-IN"}), 0),
							(200, "application/json", json.dumps({"audios": [base64.b64encode(b"RIFFwav").decode()]}), 0)]
		cfg = ProviderConfig(name="s", provider_type="Sarvam", base_url=f"http://127.0.0.1:{self.port}", api_key=SECRET,
							 model="saaras:v3", timeout=5)
		self.assertEqual(SarvamAdapter(cfg).transcribe(b"audio", "audio/webm", None)["text"], "aaj ki sales")
		self.assertEqual(Script.requests[0]["path"], "/speech-to-text")
		self.assertEqual(Script.requests[0]["headers"]["api-subscription-key"], SECRET)
		audio, mime = SarvamAdapter(cfg).synthesize("Namaste", "hi", "shubh")
		self.assertEqual((audio, mime), (b"RIFFwav", "audio/wav"))
		self.assertEqual(Script.requests[1]["json"]["language_code"], "hi-IN")

	def test_openai_embeddings_and_transcription(self):
		Script.responses = [(200, "application/json", json.dumps({"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}), 0),
							(200, "application/json", json.dumps({"text": "hello"}), 0)]
		ad = OpenAICompatAdapter(self.cfg())
		self.assertEqual(ad.embed(["a", "b"]), [[1, 0], [0, 1]])
		self.assertEqual(ad.transcribe(b"x", "audio/webm")["text"], "hello")
		self.assertEqual(Script.requests[1]["path"], "/v1/audio/transcriptions")

	def test_vision_parts_in_both_wire_formats(self):
		msg = [{"role": "user", "content": [{"type": "text", "text": "read"}, {"type": "image", "media_type": "image/png", "data": "QUJD"}]}]
		oa = OpenAICompatAdapter.to_wire(msg)[0]["content"]
		self.assertEqual(oa[1]["image_url"]["url"], "data:image/png;base64,QUJD")
		_sys, an = AnthropicAdapter.to_wire(msg)
		self.assertEqual(an[0]["content"][1]["source"]["data"], "QUJD")

	def test_anthropic_tool_result_wire_format(self):
		from frappe_ai_assistant.frappe_ai_assistant.providers.base import ToolCall

		system, wire = AnthropicAdapter.to_wire([
			{"role": "system", "content": "s"},
			{"role": "user", "content": "q"},
			{"role": "assistant", "content": "", "tool_calls": [ToolCall("t1", "x", {"a": 1})]},
			{"role": "tool", "tool_call_id": "t1", "name": "x", "content": "r"},
		])
		self.assertEqual([m["role"] for m in wire], ["user", "assistant", "user"])
		self.assertEqual(wire[2]["content"][0]["type"], "tool_result")


class ProviderConfigTest(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_base_url_must_be_https(self):
		with self.assertRaises(ProviderNotConfigured):
			router.validate_base_url("http://evil.example.com/v1")
		router.validate_base_url("https://api.example.com/v1")
		router.validate_base_url("http://localhost:11434/v1")

	def test_key_stored_encrypted_and_masked(self):
		doc = frappe.get_doc({"doctype": "Chikku AI Provider Configuration", "provider_name": "Enc Test",
							  "provider_type": "Anthropic", "default_model": "m", "api_key": SECRET})
		doc.insert(ignore_permissions=True)
		raw = frappe.db.get_value("Chikku AI Provider Configuration", "Enc Test", "api_key")
		self.assertNotEqual(raw, SECRET)
		self.assertNotIn(SECRET, json.dumps(frappe.get_doc("Chikku AI Provider Configuration", "Enc Test").as_dict(),
											default=str))
		cfg = router.load_config("Enc Test")
		self.assertEqual(cfg.api_key, SECRET)
		self.assertNotIn(SECRET, repr(cfg))

	def test_missing_key_is_not_configured(self):
		frappe.get_doc({"doctype": "Chikku AI Provider Configuration", "provider_name": "NoKey",
						"provider_type": "OpenAI", "default_model": "m"}).insert(ignore_permissions=True)
		with self.assertRaises(ProviderNotConfigured):
			router.load_config("NoKey")
		with self.assertRaises(ProviderNotConfigured):
			router.load_config("")

	def test_disabled_provider_rejected(self):
		frappe.get_doc({"doctype": "Chikku AI Provider Configuration", "provider_name": "Off",
						"provider_type": "OpenAI", "default_model": "m", "api_key": SECRET,
						"enabled": 0}).insert(ignore_permissions=True)
		with self.assertRaises(ProviderNotConfigured):
			router.load_config("Off")

	def test_circuit_breaker(self):
		router.record_success("cb-test")
		for _ in range(router.BREAKER_FAILURES):
			router.record_failure("cb-test")
		self.assertTrue(router.breaker_open("cb-test"))
		router.record_success("cb-test")
		self.assertFalse(router.breaker_open("cb-test"))
