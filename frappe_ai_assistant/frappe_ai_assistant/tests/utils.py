"""Test helpers. All tests run inside one transaction that the runner rolls back."""

from __future__ import annotations

import contextlib
import unittest
from unittest import mock

import frappe

from frappe_ai_assistant.frappe_ai_assistant.providers.base import ChatResult, ProviderAdapter, ToolCall

TEST_DOMAIN = "chikku-test.invalid"


def make_user(local: str, roles: list[str], first_name: str | None = None) -> str:
	email = f"{local}@{TEST_DOMAIN}"
	if not frappe.db.exists("User", email):
		u = frappe.get_doc({
			"doctype": "User", "email": email, "first_name": first_name or local.title(),
			"user_type": "System User", "send_welcome_email": 0, "enabled": 1,
		})
		u.flags.ignore_permissions = True
		u.flags.no_welcome_mail = True
		u.insert()
		u.add_roles(*roles)
	frappe.clear_cache(user=email)
	return email


@contextlib.contextmanager
def as_user(user: str):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(previous)


class FakeAdapter(ProviderAdapter):
	"""Scripted provider: returns the queued ChatResults in order, records requests."""

	def __init__(self, script: list[ChatResult], config=None):
		self.script = list(script)
		self.requests = []
		self.config = config

	def chat(self, messages, tools=None, **kw):
		self.requests.append({"messages": [dict(m) for m in messages], "tools": [t.name for t in tools or []]})
		on_delta = kw.get("on_delta")
		res = self.script.pop(0)
		if on_delta and res.text:
			for part in res.text.split(" "):
				on_delta(part + " ")
		return res


def text(t: str, tin=10, tout=5) -> ChatResult:
	return ChatResult(text=t, input_tokens=tin, output_tokens=tout, model="fake-model")


def tool(name: str, args: dict, call_id="call_1") -> ChatResult:
	return ChatResult(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)], input_tokens=10, output_tokens=5,
					  model="fake-model")


@contextlib.contextmanager
def fake_provider(script: list[ChatResult]):
	"""Configure a provider row + patch the adapter factory so no network call is made."""
	from frappe_ai_assistant.frappe_ai_assistant.providers import router

	name = "Chikku Test Provider"
	if not frappe.db.exists("Chikku AI Provider Configuration", name):
		frappe.get_doc({"doctype": "Chikku AI Provider Configuration", "provider_name": name,
						"provider_type": "OpenAI", "default_model": "fake-model", "enabled": 1,
						"api_key": "sk-test-SECRET-0123456789abcdef"}).insert(ignore_permissions=True)
	settings = frappe.get_doc("Chikku AI Settings")
	old = settings.reasoning_provider
	frappe.db.set_single_value("Chikku AI Settings", "reasoning_provider", name)
	frappe.clear_document_cache("Chikku AI Settings", "Chikku AI Settings")
	adapter = FakeAdapter(script)
	with mock.patch.object(router, "get_adapter", lambda cfg: adapter):
		try:
			yield adapter
		finally:
			frappe.db.set_single_value("Chikku AI Settings", "reasoning_provider", old)
			frappe.clear_document_cache("Chikku AI Settings", "Chikku AI Settings")


@contextlib.contextmanager
def capture_realtime():
	events = []

	def fake(event, message=None, *a, **kw):
		events.append({"event": event, "message": message, "user": kw.get("user"), "room": kw.get("room")})

	with mock.patch("frappe.publish_realtime", fake):
		yield events


@contextlib.contextmanager
def settings_override(**values):
	old = {k: frappe.db.get_single_value("Chikku AI Settings", k) for k in values}
	for k, v in values.items():
		frappe.db.set_single_value("Chikku AI Settings", k, v)
	frappe.clear_document_cache("Chikku AI Settings", "Chikku AI Settings")
	try:
		yield
	finally:
		for k, v in old.items():
			frappe.db.set_single_value("Chikku AI Settings", k, v)
		frappe.clear_document_cache("Chikku AI Settings", "Chikku AI Settings")


def reset_limits(*users):
	"""Rate-limit counters live in Redis and are not rolled back with the database."""
	for user in users:
		for bucket in ("min", "day", "confirm", "upload"):
			frappe.cache.delete(frappe.cache.make_key(f"chikku_ai:rl:{bucket}:{user}"))


class ChikkuTestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.alice = make_user("alice", ["Chikku AI User", "Sales User", "Stock User"], "Alice")
		cls.bob = make_user("bob", ["Chikku AI User", "Sales User"], "Bob")
		cls.nobody = make_user("noaccess", ["Sales User"], "Nora")  # no Chikku role

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.clear_messages()
		reset_limits(self.alice, self.bob, self.nobody)

	def tearDown(self):
		frappe.set_user("Administrator")
