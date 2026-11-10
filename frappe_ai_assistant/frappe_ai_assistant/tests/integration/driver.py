"""Live integration test of Chikku AI against the RUNNING bench (web, RQ worker, socket.io).

TEST SITES ONLY. It temporarily creates a test user, a local mock provider and switches
Chikku AI Settings to that provider (draft actions on), then removes everything again.
Run from frappe-bench/sites with the bench Python and Node >= 20 on PATH:
	CHIKKU_IT_SITE=ambatest.local CHIKKU_IT_PUBLIC_HOST=erptest.ambagarments.com \
		../env/bin/python -I ../apps/amba_garments/amba_garments/chikku_ai/tests/integration/driver.py


Real HTTP login -> Desk HTML -> API -> RQ worker -> provider HTTP (local scripted OpenAI-compatible
server) -> tools -> socket.io realtime -> draft confirmation. Everything it creates is deleted at the end.
Run with the bench Python from frappe-bench/sites.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import urllib3

urllib3.disable_warnings()

SITE = os.environ.get("CHIKKU_IT_SITE", "ambatest.local")
PUBLIC_HOST = os.environ.get("CHIKKU_IT_PUBLIC_HOST", "erptest.ambagarments.com")
BASE = "http://127.0.0.1:8000"
USER = "chikku.integration@chikku-test.invalid"
PWD = "Int3gration!" + os.urandom(6).hex()
PROVIDER = "Local Mock Provider (integration test)"
PORT = 18765
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []
PROVIDER_LOG = []


def check(name, ok, detail=""):
	RESULTS.append(bool(ok))
	print(("PASS" if ok else "FAIL"), "|", name, "|", str(detail)[:300], flush=True)


# ------------------------------------------------------------------ scripted provider
class Mock(BaseHTTPRequestHandler):
	def log_message(self, *a):
		pass

	def do_POST(self):
		body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
		tools_done = sum(1 for m in body["messages"] if m["role"] == "tool")
		PROVIDER_LOG.append({"tools_offered": [t["function"]["name"] for t in body.get("tools", [])],
							 "tool_results_seen": tools_done, "auth_header": "Authorization" in self.headers,
							 "system_has_rules": "untrusted_data" in body["messages"][0]["content"]})
		self.send_response(200)
		self.send_header("Content-Type", "text/event-stream")
		self.end_headers()

		def emit(obj):
			self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
			self.wfile.flush()

		if tools_done == 0:
			emit({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
				"name": "search_customers", "arguments": json.dumps({"search_text": CUSTOMER[:6], "limit": 3})}}]}}]})
		elif tools_done == 1:
			emit({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c2", "function": {
				"name": "prepare_sales_order_draft", "arguments": json.dumps(DRAFT_ARGS)}}]}}]})
		else:
			for word in "Maine draft Sales Order ka preview taiyaar kar diya hai. Please check karke Create draft dabaiye.".split():
				emit({"choices": [{"delta": {"content": word + " "}}]})
				time.sleep(0.05)
		emit({"choices": [], "usage": {"prompt_tokens": 100 + tools_done, "completion_tokens": 20}})
		self.wfile.write(b"data: [DONE]\n\n")


# ------------------------------------------------------------------ setup / cleanup via frappe
def frappe_setup():
	import frappe

	frappe.init(site=SITE, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	global CUSTOMER, DRAFT_ARGS
	company = "Amba Garments Limited"
	CUSTOMER = frappe.get_all("Customer", {"disabled": 0}, pluck="name", order_by="modified desc", limit=1)[0]
	item = frappe.get_all("Item", {"disabled": 0, "is_sales_item": 1, "has_variants": 0}, pluck="name",
						  order_by="modified desc", limit=1)[0]
	wh = frappe.get_all("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, pluck="name", limit=1)[0]
	tax = frappe.get_all("Sales Taxes and Charges Template", {"company": company, "disabled": 0}, pluck="name", limit=1)[0]
	DRAFT_ARGS = {"customer": CUSTOMER, "company": company, "delivery_date": str(frappe.utils.add_days(frappe.utils.today(), 7)),
				  "set_warehouse": wh, "taxes_and_charges": tax, "items": [{"item_code": item, "qty": 3, "rate": 250}]}
	if not frappe.db.exists("User", USER):
		u = frappe.get_doc({"doctype": "User", "email": USER, "first_name": "Kamal", "user_type": "System User",
							"send_welcome_email": 0})
		u.flags.no_welcome_mail = True
		u.insert(ignore_permissions=True)
		u.add_roles("Chikku AI User", "Sales User", "Stock User")
	from frappe.utils.password import update_password

	update_password(USER, PWD)
	if not frappe.db.exists("Chikku AI Provider Configuration", PROVIDER):
		frappe.get_doc({"doctype": "Chikku AI Provider Configuration", "provider_name": PROVIDER,
						"provider_type": "OpenAI-Compatible", "base_url": f"http://127.0.0.1:{PORT}/v1",
						"default_model": "mock-model", "enabled": 1, "max_retries": 0}).insert(ignore_permissions=True)
	s = frappe.get_doc("Chikku AI Settings")
	saved = {"reasoning_provider": s.reasoning_provider, "draft_actions_enabled": s.draft_actions_enabled}
	s.reasoning_provider = PROVIDER
	s.draft_actions_enabled = 1
	s.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_cache(user=USER)
	frappe.destroy()
	return saved


def frappe_cleanup(saved, created_so):
	import frappe

	frappe.init(site=SITE, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	evidence = {
		"conversations": frappe.db.count("Chikku AI Conversation", {"user": USER}),
		"messages": frappe.db.count("Chikku AI Message", {"owner": USER}),
		"tool_calls": frappe.get_all("Chikku AI Tool Call", {"user": USER}, ["tool_name", "status"]),
		"usage": frappe.get_all("Chikku AI Usage Log", {"user": USER}, ["provider", "status", "input_tokens", "output_tokens"]),
		"audit": frappe.get_all("Chikku AI Audit Log", {"user": USER}, ["event_type", "severity"]),
		"action_requests": frappe.get_all("Chikku AI Action Request", {"user": USER}, ["status", "result_name"]),
	}
	print("EVIDENCE " + json.dumps(evidence, default=str), flush=True)
	if created_so and frappe.db.exists("Sales Order", created_so):
		so = frappe.get_doc("Sales Order", created_so)
		if so.docstatus == 0 and so.owner == USER:
			frappe.delete_doc("Sales Order", created_so, ignore_permissions=True, force=True)
	convs = frappe.get_all("Chikku AI Conversation", {"user": USER}, pluck="name")
	for dt in ("Chikku AI Tool Call", "Chikku AI Usage Log", "Chikku AI Audit Log", "Chikku AI Action Request"):
		frappe.db.delete(dt, {"user": USER})
	for c in convs:
		frappe.db.delete("Chikku AI Message", {"conversation": c})
		frappe.db.delete("Chikku AI Conversation", {"name": c})
	frappe.db.delete("Chikku AI Usage Log", {"provider": PROVIDER})
	frappe.db.delete("Chikku AI User Preference", {"user": USER})
	s = frappe.get_doc("Chikku AI Settings")
	s.update(saved)
	s.save(ignore_permissions=True)
	frappe.delete_doc("Chikku AI Provider Configuration", PROVIDER, ignore_permissions=True, force=True)
	frappe.db.delete("Chikku AI Audit Log", {"event_type": ["in", ["provider_config_deleted", "provider_config_changed",
																   "settings_changed"]],
											 "creation": [">", frappe.utils.add_to_date(None, hours=-1)]})
	frappe.delete_doc("User", USER, ignore_permissions=True, force=True)
	frappe.db.commit()
	left = {dt: frappe.db.count(dt) for dt in ("Chikku AI Conversation", "Chikku AI Message", "Chikku AI Provider Configuration")}
	print("CLEANUP " + json.dumps({"left": left, "user_exists": bool(frappe.db.exists("User", USER)),
								   "so_exists": bool(created_so and frappe.db.exists("Sales Order", created_so)),
								   "reasoning_provider": frappe.db.get_single_value("Chikku AI Settings", "reasoning_provider"),
								   "draft_actions_enabled": frappe.db.get_single_value("Chikku AI Settings", "draft_actions_enabled")}),
		  flush=True)
	frappe.destroy()


# ------------------------------------------------------------------ the run
def main():
	server = ThreadingHTTPServer(("127.0.0.1", PORT), Mock)
	threading.Thread(target=server.serve_forever, daemon=True).start()
	saved = frappe_setup()
	created_so = None
	try:
		created_so = run_http()
	finally:
		server.shutdown()
		frappe_cleanup(saved, created_so)
	print(f"SUMMARY {sum(RESULTS)} pass / {len(RESULTS) - sum(RESULTS)} fail", flush=True)


def run_http():
	h = {"Host": SITE}
	s = requests.Session()
	r = s.post(f"{BASE}/api/method/login", data={"usr": USER, "pwd": PWD}, headers=h)
	check("Real login over HTTP", r.status_code == 200 and "sid" in s.cookies, r.status_code)
	sid = s.cookies.get("sid")

	desk = s.get(f"{BASE}/desk", headers=h)
	html = desk.text
	check("Desk page loads", desk.status_code == 200, desk.status_code)
	check("Desk includes Chikku launcher bundle", "chikku_ai.bundle" in html and ".js" in html,
		  re.findall(r'/assets/amba_garments/dist/js/chikku_ai\.bundle\.[A-Z0-9]+\.js', html)[:1])
	check("Desk includes Chikku CSS bundle", re.search(r"chikku_ai\.bundle\.[A-Z0-9]+\.css", html) is not None)
	check("Existing amba script still included", "restrict_backdate.js" in html)
	m = re.search(r'"chikku_ai":\s*(\{[^}]*\})', html)
	check("Boot info exposes launcher config only", m and "api_key" not in m.group(1) and "enabled" in m.group(1),
		  m.group(1) if m else None)
	csrf = re.search(r'csrf_token\s*=\s*"([^"]+)"', html)
	csrf = csrf.group(1) if csrf else ""
	check("CSRF token found in Desk", bool(csrf))
	asset = re.search(r'/assets/amba_garments/dist/js/chikku_ai\.bundle\.[A-Z0-9]+\.js', html).group(0)
	launcher = requests.get("https://127.0.0.1" + asset, headers={"Host": PUBLIC_HOST}, verify=False)
	check("Launcher bundle served", launcher.status_code == 200 and "chikku-ai-launcher" in launcher.text, launcher.status_code)

	api = lambda method, **data: s.post(f"{BASE}/api/method/frappe_ai_assistant.frappe_ai_assistant.api.{method}", json=data,
										headers={**h, "X-Frappe-CSRF-Token": csrf, "Accept": "application/json"})

	no_csrf = s.post(f"{BASE}/api/method/frappe_ai_assistant.frappe_ai_assistant.api.chat.open_session", json={}, headers=h)
	check("API rejects requests without CSRF token", no_csrf.status_code in (400, 403), no_csrf.status_code)
	anon = requests.post(f"{BASE}/api/method/frappe_ai_assistant.frappe_ai_assistant.api.chat.open_session", json={}, headers=h)
	check("API rejects anonymous users", anon.status_code in (401, 403), anon.status_code)

	r = api("chat.open_session")
	sess = r.json().get("message", {})
	check("open_session greets by server-side first name", "Kamal" in (sess.get("greeting") or ""), sess.get("greeting"))
	check("Provider shown as ready", sess.get("provider_ready") is True)
	check("open_session response has no secrets", "api_key" not in r.text and "sk-" not in r.text)

	listener = subprocess.Popen(["node", os.path.join(HERE, "listen.js"), sid, SITE, "75", PUBLIC_HOST],
								stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
	first = listener.stdout.readline()
	check("Socket.io realtime connection as the user", '"connected"' in first, first.strip())

	so_name = frappe_any_sales_order()
	t0 = time.time()
	r = api("chat.send_message", text="J K wale customer ke liye 3 qty ka draft sales order bana do",
			context=json.dumps({"kind": "form", "route": ["Form", "Sales Order", so_name]}),
			idempotency_key="int-" + os.urandom(4).hex())
	body = r.json()
	check("send_message returns quickly (no LLM call in request)", r.status_code == 200 and time.time() - t0 < 5,
		  f"{r.status_code} in {time.time() - t0:.2f}s")
	conv = body["message"]["conversation"]

	events, token, action_request, reply = [], None, None, None
	deadline = time.time() + 70
	while time.time() < deadline:
		line = listener.stdout.readline()
		if not line:
			break
		try:
			evt = json.loads(line)
		except ValueError:
			continue
		events.append(evt)
		data = evt.get("data", {})
		if evt.get("event") == "chikku_ai_event":
			if data.get("type") == "action_token":
				token, action_request = data["token"], data["action_request"]
			if data.get("type") == "done":
				reply = data
				break
	types = [e["data"].get("type") for e in events if e.get("event") == "chikku_ai_event"]
	check("Background worker processed the turn", reply is not None, f"{time.time() - t0:.1f}s")
	check("Realtime events received (status, tool, delta, action_token, done)",
		  {"status", "tool", "delta", "action_token", "done"} <= set(types), sorted(set(types)))
	check("Provider received tools and safety rules", PROVIDER_LOG and PROVIDER_LOG[0]["system_has_rules"]
		  and "search_customers" in PROVIDER_LOG[0]["tools_offered"], PROVIDER_LOG[:1])
	check("Three provider rounds (tool, draft tool, answer)", len(PROVIDER_LOG) == 3, len(PROVIDER_LOG))
	check("No list_update broadcast for Chikku documents",
		  not any(e.get("event") == "list_update" and str(e["data"].get("doctype", "")).startswith("Chikku")
				  for e in events))
	payload = (reply or {}).get("payload") or {}
	check("Reply stored as Completed with sanitised HTML", payload.get("status") == "Completed"
		  and "<p>" in (payload.get("content_html") or ""), payload.get("status"))
	block_types = [b["type"] for b in payload.get("blocks") or []]
	check("Reply carries table and draft preview blocks", "table" in block_types and "action_preview" in block_types,
		  block_types)
	check("Token is not stored in the reply payload", token and token not in json.dumps(payload))

	msgs = s.get(f"{BASE}/api/method/frappe_ai_assistant.frappe_ai_assistant.api.chat.get_messages",
				 params={"conversation": conv}, headers=h).json()["message"]["messages"]
	check("History returns user + assistant messages in order", [m["role"] for m in msgs] == ["user", "assistant"],
		  [m["role"] for m in msgs])

	bad = api("actions.confirm", action_request=action_request, token="x" * 43).json().get("message", {})
	check("Wrong token rejected", bad.get("ok") is False, bad)
	ok = api("actions.confirm", action_request=action_request, token=token).json().get("message", {})
	check("Confirm creates the draft", ok.get("ok") is True and ok.get("docstatus") == 0, ok)
	replay = api("actions.confirm", action_request=action_request, token=token).json().get("message", {})
	check("Replay of the same token rejected", replay.get("ok") is False, replay)

	listener.kill()
	return ok.get("name")


def frappe_any_sales_order():
	import frappe

	frappe.init(site=SITE, sites_path=".")
	frappe.connect()
	name = frappe.get_all("Sales Order", pluck="name", order_by="modified desc", limit=1)[0]
	frappe.destroy()
	return name


if __name__ == "__main__":
	main()
	sys.stdout.flush()
