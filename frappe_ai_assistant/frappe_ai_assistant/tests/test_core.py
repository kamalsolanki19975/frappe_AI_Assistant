"""Settings, access, conversations, messaging API, greeting, language, rendering, limits, uploads, lint."""

import ast
import json
import os
from unittest import mock

import frappe

from frappe_ai_assistant.frappe_ai_assistant import CONVERSATION, MESSAGE
from frappe_ai_assistant.frappe_ai_assistant.ai import greeting, language, render
from frappe_ai_assistant.frappe_ai_assistant.api import chat, files
from frappe_ai_assistant.frappe_ai_assistant.security import access, limits

from .utils import ChikkuTestCase, as_user, settings_override


@__import__("contextlib").contextmanager
def capture_events():
	events = []

	def fake(event, message=None, *a, **kw):
		events.append({"event": event, "message": message, "docname": kw.get("docname")})

	with mock.patch("frappe.publish_realtime", fake):
		yield events


class SettingsAndAccessTest(ChikkuTestCase):
	def test_single_settings_with_defaults(self):
		s = frappe.get_doc("Chikku AI Settings")
		self.assertTrue(frappe.get_meta("Chikku AI Settings").issingle)
		self.assertEqual(s.assistant_name, "Chikku AI")
		self.assertEqual(int(s.backdate_days), 3)
		self.assertEqual(int(s.draft_actions_enabled), 0)

	def test_settings_validation(self):
		s = frappe.get_doc("Chikku AI Settings")
		s.max_tool_calls_per_turn = 99
		with self.assertRaises(frappe.ValidationError):
			s.save(ignore_permissions=True)

	def test_settings_not_readable_by_normal_users(self):
		with as_user(self.alice):
			self.assertFalse(frappe.has_permission("Chikku AI Settings", "read"))
			self.assertFalse(frappe.has_permission("Chikku AI Provider Configuration", "read"))

	def test_access_gate(self):
		self.assertTrue(access.has_access(self.alice))
		self.assertFalse(access.has_access(self.nobody))
		with as_user(self.nobody), self.assertRaises(frappe.PermissionError):
			chat.open_session()

	def test_kill_switch(self):
		with settings_override(enabled=0), as_user(self.alice), self.assertRaises(frappe.PermissionError):
			chat.open_session()

	def test_boot_info_has_no_secrets_and_respects_access(self):
		from frappe_ai_assistant.frappe_ai_assistant.boot import extend_bootinfo

		with as_user(self.alice):
			b = frappe._dict()
			extend_bootinfo(b)
			self.assertEqual(set(b.chikku_ai), {"enabled", "assistant_name", "avatar"})
		with as_user(self.nobody):
			b = frappe._dict()
			extend_bootinfo(b)
			self.assertNotIn("chikku_ai", b)


class DeskVisibilityTest(ChikkuTestCase):
	def test_admin_log_lists_open_in_desk_but_private_messages_do_not(self):
		from frappe.utils.user import UserPermissions

		mgr = frappe.get_doc("User", self.bob)
		mgr.add_roles("Chikku AI Manager")
		frappe.clear_cache(user=self.bob)
		with as_user(self.bob):
			perms = UserPermissions(self.bob)
			perms.build_permissions()
			for dt in ("Chikku AI Audit Log", "Chikku AI Usage Log", "Chikku AI Tool Call",
					   "Chikku AI Action Request", "Chikku AI Settings", "Chikku AI Provider Configuration"):
				self.assertIn(dt, perms.can_read, dt)
			self.assertNotIn("Chikku AI Message", perms.can_read)
		mgr.remove_roles("Chikku AI Manager")
		frappe.clear_cache(user=self.bob)

	def test_no_site_wide_list_update_for_chikku_records(self):
		from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit, log_tool_call

		with capture_events() as events, as_user(self.alice):
			conv = chat.create_conversation()["conversation"]
			audit("unit_test_event", user=self.alice)
			log_tool_call(tool_name="t", kind="read", status="OK", args={}, user=self.alice)
			chat.set_language("English")
			chat.set_language("Auto")
		self.assertEqual([e for e in events if e["event"] == "list_update"], [])
		self.assertTrue(any(e["event"] == "doc_update" and e["docname"] == conv for e in events))


class ConversationTest(ChikkuTestCase):
	def _send(self, text, **kw):
		with mock.patch("frappe.enqueue") as enq:
			r = chat.send_message(text=text, **kw)
		return r, enq

	def test_send_message_stores_and_enqueues_as_user(self):
		with as_user(self.alice):
			r, enq = self._send("Hello there")
			conv = r["conversation"]
			self.assertEqual(frappe.db.get_value(CONVERSATION, conv, "user"), self.alice)
			m = frappe.get_doc(MESSAGE, r["message"]["name"])
			self.assertEqual((m.role, m.status, m.seq, m.owner), ("user", "Queued", 1, self.alice))
			kwargs = enq.call_args.kwargs
			self.assertEqual(kwargs["message"], m.name)
			self.assertEqual(kwargs["queue"], "long")
			self.assertTrue(kwargs["enqueue_after_commit"])

	def test_idempotent_resend(self):
		with as_user(self.alice):
			r1, _ = self._send("Same", idempotency_key="k-123")
			r2, enq = self._send("Same", conversation=r1["conversation"], idempotency_key="k-123")
			self.assertTrue(r2.get("duplicate"))
			self.assertEqual(r1["message"]["name"], r2["message"]["name"])
			enq.assert_not_called()

	def test_one_reply_at_a_time(self):
		with as_user(self.alice):
			r1, _ = self._send("first")
			with self.assertRaises(frappe.ValidationError):
				self._send("second", conversation=r1["conversation"])

	def test_user_b_cannot_access_user_a_conversation(self):
		with as_user(self.alice):
			r, _ = self._send("private stuff")
			conv = r["conversation"]
			msg = r["message"]["name"]
		with as_user(self.bob):
			for fn in (lambda: chat.get_messages(conv), lambda: chat.open_session(conv),
					   lambda: chat.rename_conversation(conv, "x"), lambda: chat.archive_conversation(conv),
					   lambda: chat.cancel_turn(msg), lambda: self._send("inject", conversation=conv)):
				with self.assertRaises(frappe.DoesNotExistError):
					fn()
			self.assertFalse(frappe.has_permission(CONVERSATION, "read", doc=conv))
			self.assertFalse(frappe.has_permission(MESSAGE, "read", doc=msg))
			self.assertEqual(frappe.get_list(CONVERSATION, filters={"name": conv}), [])
			self.assertEqual(frappe.get_list(MESSAGE, filters={"name": msg}), [])
			self.assertNotIn(conv, [c.name for c in chat.list_conversations()])

	def test_manager_cannot_read_private_conversations(self):
		mgr = frappe.get_doc("User", self.bob)
		mgr.add_roles("Chikku AI Manager")
		frappe.clear_cache(user=self.bob)
		with as_user(self.alice):
			r, _ = self._send("secret")
		with as_user(self.bob):
			self.assertFalse(frappe.has_permission(CONVERSATION, "read", doc=r["conversation"]))
			self.assertEqual(frappe.get_list(MESSAGE, filters={"conversation": r["conversation"]}), [])

	def test_client_supplied_user_is_ignored(self):
		with as_user(self.alice):
			# Creating a conversation "for" someone else is refused outright ...
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc({"doctype": CONVERSATION, "user": self.bob}).insert()
			# ... and the API never takes a user argument: the owner is always the session user.
			conv = chat.create_conversation()["conversation"]
			self.assertEqual(frappe.db.get_value(CONVERSATION, conv, "user"), self.alice)

	def test_message_pagination(self):
		with as_user(self.alice):
			conv = chat.create_conversation()["conversation"]
			for i in range(1, 8):
				frappe.get_doc({"doctype": MESSAGE, "conversation": conv, "role": "user", "seq": i,
								"content": f"m{i}", "status": "Completed"}).insert(ignore_permissions=True)
			page = chat.get_messages(conv, limit=3)
			self.assertEqual([m["seq"] for m in page["messages"]], [5, 6, 7])
			self.assertTrue(page["has_more"])
			older = chat.get_messages(conv, before_seq=5, limit=3)
			self.assertEqual([m["seq"] for m in older["messages"]], [2, 3, 4])

	def test_input_validation(self):
		with as_user(self.alice):
			with self.assertRaises(frappe.ValidationError):
				self._send("   ")
			with self.assertRaises(frappe.ValidationError):
				self._send("x" * 9000)

	def test_forged_context_is_revalidated(self):
		other = frappe.get_all("Sales Invoice", pluck="name", limit=1)[0]
		with as_user(self.bob):  # Bob (Sales User) has no Sales Invoice access via Accounts roles?
			allowed = frappe.has_permission("Sales Invoice", "read", other)
			r, _ = self._send("why?", context=json.dumps({"kind": "form", "route": ["Form", "Sales Invoice", other],
															"doctype": "Sales Invoice", "docname": other}))
			ctx = json.loads(frappe.db.get_value(MESSAGE, r["message"]["name"], "context_json") or "{}")
			if allowed:
				self.assertEqual(ctx.get("name"), other)
			else:
				self.assertEqual(ctx.get("kind"), "unavailable")
				self.assertNotIn(other, json.dumps(ctx))

	def test_language_preference(self):
		with as_user(self.alice):
			chat.set_language("Hinglish")
			self.assertEqual(frappe.db.get_value("Chikku AI User Preference", self.alice, "preferred_language"),
							 "Hinglish")
			with self.assertRaises(frappe.ValidationError):
				chat.set_language("Klingon")
			chat.set_language("Auto")


class GreetingLanguageRenderTest(ChikkuTestCase):
	def test_greeting_uses_server_first_name(self):
		s = frappe.get_doc("Chikku AI Settings")
		with as_user(self.alice):
			self.assertTrue(greeting.build(s, "en").startswith("Hey Alice!"))
			self.assertIn("Alice", greeting.build(s, "hinglish"))
			self.assertIn("Alice", greeting.build(s, "hi"))
			r = chat.open_session()
			self.assertIn("Alice", r["greeting"])

	def test_greeting_fallback_and_no_template_injection(self):
		s = frappe.get_doc("Chikku AI Settings")
		s.welcome_message = "Hi {first_name} {__class__} {assistant_name}"
		with mock.patch.object(greeting, "first_name", return_value=None):
			self.assertEqual(greeting.build(s, "en"), "Hi there {__class__} Chikku AI")

	def test_no_greeting_for_existing_conversation(self):
		with as_user(self.alice):
			with mock.patch("frappe.enqueue"):
				conv = chat.send_message(text="hello")["conversation"]
			self.assertIsNone(chat.open_session(conv)["greeting"])

	def test_saarthi_erp_branding(self):
		from frappe_ai_assistant.frappe_ai_assistant.ai.prompts import build_system_prompt
		from frappe_ai_assistant.frappe_ai_assistant.tools.registry import REGISTRY, available_tools

		s = frappe.get_doc("Chikku AI Settings")
		with as_user(self.alice):
			prompt = build_system_prompt(s, language="en", user_first_name="Alice", response_style=None,
										 has_tools=True, context_summary=None)
		self.assertIn("built into SaarthiERP", prompt)
		self.assertIn('Always call the system "SaarthiERP"', prompt)
		available_tools(s)
		for t in REGISTRY.values():
			self.assertNotIn("ERPNext", t.description + str(t.spec().parameters), t.name)
		for code in ("hi", "hinglish"):
			self.assertNotIn("ERPNext", language.instruction(code))

	def test_language_detection(self):
		cases = {
			"Why is this order still pending?": "en",
			"यह ऑर्डर अभी तक पेंडिंग क्यों है?": "hi",
			"Yeh order abhi tak pending kyun hai?": "hinglish",
			"mujhe pichhle mahine ki sales batao": "hinglish",
			"Show me the stock of item ABC-123": "en",
			"kitna stock hai": "hinglish",
		}
		for text, expected in cases.items():
			self.assertEqual(language.detect(text), expected, text)

	def test_effective_language_respects_settings(self):
		s = frappe._dict(default_language="Auto", auto_detect_language=1, hindi_enabled=0, hinglish_enabled=1)
		self.assertEqual(language.effective_language("यह क्या है", None, s), "en")
		self.assertEqual(language.effective_language("hello", "Hinglish", s), "hinglish")

	def test_markdown_is_sanitised(self):
		html = render.to_html('Hi <script>alert(1)</script> <img src=x onerror=alert(1)> '
							  '[x](javascript:alert(1)) **bold**\n\n| a | b |\n|---|---|\n| 1 | 2 |')
		self.assertNotIn("<script", html)
		self.assertNotIn("<img", html)  # raw HTML is escaped to inert text
		self.assertNotIn("javascript:", html)
		self.assertIn("<strong>bold</strong>", html)
		self.assertIn("<table>", html)


class LimitsAndUploadsTest(ChikkuTestCase):
	def test_rate_limit(self):
		s = frappe._dict(user_requests_per_minute=2, user_requests_per_day=0)
		user = f"rl-{frappe.generate_hash(length=6)}@x"
		limits.check_message_rate(s, user)
		limits.check_message_rate(s, user)
		with self.assertRaises(limits.LimitExceeded):
			limits.check_message_rate(s, user)

	def test_daily_token_budget(self):
		frappe.get_doc({"doctype": "Chikku AI Usage Log", "user": self.alice, "input_tokens": 900,
						"output_tokens": 200, "status": "Success"}).insert(ignore_permissions=True)
		s = frappe._dict(user_daily_token_limit=1000, monthly_cost_limit=0)
		with self.assertRaises(limits.LimitExceeded):
			limits.check_budgets(s, self.alice)

	def test_file_checks(self):
		png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
		self.assertEqual(files.check_file("pic.png", png, 10), "pic.png")
		self.assertEqual(files.check_file("../../etc/passwd.csv", b"a,b\n1,2", 10), "passwd.csv")
		for name, content in [("run.exe", b"MZ"), ("fake.pdf", b"hello"), ("x.xlsm", b"PK"),
							  ("big.csv", b"a" * (2 * 1024 * 1024)), ("bin.txt", b"\x00\x01\x02"),
							  ("macro.xlsx", b"PK\x03\x04" + b"\x00" * 26 + b"vbaProject.bin")]:
			with self.assertRaises(frappe.ValidationError, msg=name):
				files.check_file(name, content, 1)

	def test_attachment_must_belong_to_own_conversation(self):
		with as_user(self.alice):
			conv = chat.create_conversation()["conversation"]
		with as_user(self.bob):
			bob_conv = chat.create_conversation()["conversation"]
			f = frappe.get_doc({"doctype": "File", "file_name": "chikku-test-note.txt", "content": b"hello",
								"is_private": 1, "attached_to_doctype": CONVERSATION,
								"attached_to_name": bob_conv}).insert(ignore_permissions=True)
		try:
			with as_user(self.alice), self.assertRaises(frappe.DoesNotExistError):
				chat._attachments(conv, [f.name])
			with as_user(self.bob):
				self.assertEqual(chat._attachments(bob_conv, [f.name])[0]["file_name"], "chikku-test-note.txt")
		finally:
			path = f.get_full_path()
			if os.path.exists(path):
				os.remove(path)


class CodeSafetyLintTest(ChikkuTestCase):
	"""Tools and ERP services must use permission-aware APIs only."""

	FORBIDDEN_CALLS = {("frappe", "get_all"), ("db", "sql"), ("db", "get_all"), ("frappe", "get_value")}

	def test_no_permission_bypass_in_tools_and_services(self):
		base = os.path.dirname(os.path.dirname(__file__))
		problems = []
		for folder in ("tools", os.path.join("services", "erp")):
			for fn in os.listdir(os.path.join(base, folder)):
				if not fn.endswith(".py"):
					continue
				path = os.path.join(base, folder, fn)
				tree = ast.parse(open(path).read())
				for node in ast.walk(tree):
					if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
						owner = node.func.value
						owner_name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
						if (owner_name, node.func.attr) in self.FORBIDDEN_CALLS:
							problems.append(f"{folder}/{fn}:{node.lineno} {owner_name}.{node.func.attr}")
						for kw in node.keywords:
							if kw.arg == "ignore_permissions" and getattr(kw.value, "value", None) is True:
								problems.append(f"{folder}/{fn}:{node.lineno} ignore_permissions=True")
		# tokens are written through security/tokens.py, never from tools/services
		self.assertEqual(problems, [])
