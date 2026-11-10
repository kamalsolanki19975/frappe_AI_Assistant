"""R2 live integration test against the RUNNING bench (web, RQ workers, socket.io, MariaDB FULLTEXT).

TEST SITES ONLY. Creates a temporary user, scripted local AI + speech server, temporary provider settings, then
removes everything (including customers it imported and files on disk).
Run from frappe-bench/sites with the bench Python and Node >= 20 on PATH:
	CHIKKU_IT_SITE=ambatest.local CHIKKU_IT_PUBLIC_HOST=erptest.ambagarments.com \\
		../env/bin/python -I ../apps/amba_garments/amba_garments/chikku_ai/tests/integration/driver_r2.py
"""

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

SITE = os.environ.get("CHIKKU_IT_SITE", "ambatest.local")
PUBLIC_HOST = os.environ.get("CHIKKU_IT_PUBLIC_HOST", "erptest.ambagarments.com")
BASE = "http://127.0.0.1:8000"
USER = "chikku.r2@chikku-test.invalid"
PWD = "R2-Integration!" + os.urandom(6).hex()
PORT = 18766
CHAT_PROVIDER = "Local Mock AI (R2 integration)"
SPEECH_PROVIDER = "Local Mock Speech (R2 integration)"
HERE = os.path.dirname(os.path.abspath(__file__))
COMPANY = "Amba Garments Limited"
RESULTS, CALLS = [], []
TAG = os.urandom(3).hex()


def check(name, ok, detail=""):
	RESULTS.append(bool(ok))
	print(("PASS" if ok else "FAIL"), "|", name, "|", str(detail)[:300], flush=True)


class Mock(BaseHTTPRequestHandler):
	def log_message(self, *a):
		pass

	def do_POST(self):
		raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
		if self.path.endswith("/speech-to-text"):
			CALLS.append({"path": self.path, "key": self.headers.get("api-subscription-key"), "bytes": len(raw)})
			return self._json({"transcript": "is mahine ki sales dikhao", "language_code": "hi-IN"})
		body = json.loads(raw)
		msgs = body["messages"]
		user_text = next(m["content"] for m in reversed(msgs) if m["role"] == "user")
		user_text = user_text if isinstance(user_text, str) else json.dumps(user_text)
		tools_done = sum(1 for m in msgs if m["role"] == "tool")
		CALLS.append({"path": self.path, "tools": len(body.get("tools") or []), "tools_done": tools_done})
		call = None
		if tools_done == 0:
			if "excel" in user_text.lower():
				call = ("create_excel_report", {"title": f"Sales {TAG}", "metric": "sales_summary", "company": COMPANY,
												"period": "this_year", "group_by": "month"})
			elif "policy" in user_text.lower():
				call = ("search_knowledge", {"query": f"casual leave entitled {TAG}"})
			elif "import" in user_text.lower():
				fid = re.search(r"file id `(\w+)`", user_text).group(1)
				call = ("start_import", {"file": fid, "doctype": "Customer"})
		self.send_response(200)
		self.send_header("Content-Type", "text/event-stream")
		self.end_headers()
		emit = lambda o: self.wfile.write(f"data: {json.dumps(o)}\n\n".encode())  # noqa: E731
		if call:
			emit({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}}]})
		else:
			last_tool = next((m["content"] for m in reversed(msgs) if m["role"] == "tool"), "")
			answer = "Done. [1]" if "knowledge_sources" in last_tool else "Here you go."
			emit({"choices": [{"delta": {"content": answer}}]})
		emit({"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 5}})
		self.wfile.write(b"data: [DONE]\n\n")

	def _json(self, obj):
		data = json.dumps(obj).encode()
		self.send_response(200)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)


def fx():
	import frappe

	frappe.init(site=SITE, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	return frappe


def setup():
	frappe = fx()
	if not frappe.db.exists("User", USER):
		u = frappe.get_doc({"doctype": "User", "email": USER, "first_name": "Ravi", "user_type": "System User", "send_welcome_email": 0})
		u.flags.no_welcome_mail = True
		u.insert(ignore_permissions=True)
		u.add_roles("Chikku AI User", "Sales User", "Sales Master Manager", "Data Import and Export", "Accounts User")
	from frappe.utils.password import update_password

	update_password(USER, PWD)
	for name, ptype, base in [(CHAT_PROVIDER, "OpenAI-Compatible", f"http://127.0.0.1:{PORT}/v1"),
							  (SPEECH_PROVIDER, "Sarvam", f"http://127.0.0.1:{PORT}")]:
		if not frappe.db.exists("Chikku AI Provider Configuration", name):
			frappe.get_doc({"doctype": "Chikku AI Provider Configuration", "provider_name": name, "provider_type": ptype,
							"base_url": base, "default_model": "mock", "enabled": 1, "max_retries": 0,
							"api_key": "local-test-key"}).insert(ignore_permissions=True)
	s = frappe.get_doc("Chikku AI Settings")
	saved = {k: s.get(k) for k in ("reasoning_provider", "stt_provider", "stt_model")}
	s.reasoning_provider, s.stt_provider, s.stt_model = CHAT_PROVIDER, SPEECH_PROVIDER, "saaras:v3"
	s.save(ignore_permissions=True)
	group = frappe.get_all("Customer Group", {"is_group": 0}, pluck="name", limit=1)[0]
	terr = frappe.get_all("Territory", {"is_group": 0}, pluck="name", limit=1)[0]
	acc = frappe.get_all("Account", {"company": COMPANY, "account_type": "Receivable", "is_group": 0}, pluck="name", limit=1)[0]
	frappe.db.commit()
	frappe.destroy()
	return saved, group, terr, acc


def cleanup(saved):
	frappe = fx()
	paths = []
	files = frappe.get_all("File", filters={"owner": USER}, fields=["name"])
	for f in files:
		try:
			paths.append(frappe.get_doc("File", f.name).get_full_path())
		except Exception:
			pass
	customers = frappe.get_all("Customer", {"customer_name": ["like", f"R2 Test % {TAG}"]}, pluck="name")
	for c in customers:
		frappe.delete_doc("Customer", c, ignore_permissions=True, force=True)
	dis = frappe.get_all("Data Import", {"owner": USER}, pluck="name")
	for d in dis:
		frappe.db.delete("Data Import Log", {"data_import": d})
		frappe.db.delete("Data Import", {"name": d})
	kds = frappe.get_all("Chikku AI Knowledge Document", {"owner": USER}, pluck="name")
	for k in kds:
		frappe.db.delete("Chikku AI Knowledge Chunk", {"knowledge_document": k})
		frappe.db.delete("Chikku AI Knowledge Role", {"parent": k})
		frappe.db.delete("Chikku AI Knowledge Document", {"name": k})
	for dt in ("Chikku AI Tool Call", "Chikku AI Usage Log", "Chikku AI Audit Log", "Chikku AI Action Request", "Chikku AI Job",
			   "Chikku AI Import Session", "Chikku AI Alert"):
		frappe.db.delete(dt, {"user": USER})
	for c in frappe.get_all("Chikku AI Conversation", {"user": USER}, pluck="name"):
		frappe.db.delete("Chikku AI Message", {"conversation": c})
		frappe.db.delete("Chikku AI Conversation", {"name": c})
	frappe.db.delete("Chikku AI User Preference", {"user": USER})
	frappe.db.delete("File", {"owner": USER})
	frappe.db.delete("Chikku AI Usage Log", {"provider": ["in", [CHAT_PROVIDER, SPEECH_PROVIDER]]})
	s = frappe.get_doc("Chikku AI Settings")
	s.update(saved)
	s.save(ignore_permissions=True)
	for p in (CHAT_PROVIDER, SPEECH_PROVIDER):
		frappe.delete_doc("Chikku AI Provider Configuration", p, ignore_permissions=True, force=True)
	frappe.delete_doc("User", USER, ignore_permissions=True, force=True)
	frappe.db.commit()
	removed = 0
	for p in paths:
		if p and os.path.exists(p):
			os.remove(p)
			removed += 1
	print("CLEANUP " + json.dumps({"customers_removed": len(customers), "data_imports_removed": len(dis), "files_removed": removed,
								   "user_exists": bool(frappe.db.exists("User", USER)),
								   "providers_left": frappe.db.count("Chikku AI Provider Configuration"),
								   "reasoning_provider": frappe.db.get_single_value("Chikku AI Settings", "reasoning_provider")}), flush=True)
	frappe.destroy()


class Client:
	def __init__(self):
		self.s = requests.Session()
		self.h = {"Host": SITE}
		r = self.s.post(f"{BASE}/api/method/login", data={"usr": USER, "pwd": PWD}, headers=self.h)
		check("Login", r.status_code == 200, r.status_code)
		html = self.s.get(f"{BASE}/desk", headers=self.h).text
		self.csrf = re.search(r'csrf_token\s*=\s*"([^"]+)"', html).group(1)
		self.sid = self.s.cookies.get("sid")

	def post(self, method, **data):
		r = self.s.post(f"{BASE}/api/method/frappe_ai_assistant.frappe_ai_assistant.api.{method}", json=data,
						headers={**self.h, "X-Frappe-CSRF-Token": self.csrf, "Accept": "application/json"})
		return r.json().get("message") if r.ok else {"_error": r.status_code, "_body": r.text[:300]}

	def get(self, method, **params):
		r = self.s.get(f"{BASE}/api/method/frappe_ai_assistant.frappe_ai_assistant.api.{method}", params=params, headers=self.h)
		return r.json().get("message") if r.ok else {"_error": r.status_code, "_body": r.text[:300]}

	def upload(self, method, field, name, content, mime, **data):
		r = self.s.post(f"{BASE}/api/method/frappe_ai_assistant.frappe_ai_assistant.api.{method}", data=data, files={field: (name, content, mime)},
						headers={**self.h, "X-Frappe-CSRF-Token": self.csrf, "Accept": "application/json"})
		return r.json().get("message") if r.ok else {"_error": r.status_code, "_body": r.text[:300]}


def wait_reply(c, conv, before, timeout=60):
	end = time.time() + timeout
	while time.time() < end:
		msgs = c.get("chat.get_messages", conversation=conv)["messages"]
		done = [m for m in msgs if m["role"] == "assistant" and m["status"] in ("Completed", "Failed") and m["seq"] > before]
		if done:
			return done[-1]
		time.sleep(1.5)
	return None


def run(group, terr, acc):
	c = Client()
	listener = subprocess.Popen(["node", os.path.join(HERE, "listen.js"), c.sid, SITE, "240", PUBLIC_HOST],
								stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
	events = []
	threading.Thread(target=lambda: [events.append(line) for line in listener.stdout], daemon=True).start()

	sess = c.post("chat.open_session")
	caps = sess.get("capabilities", {})
	check("Session reports R2 modules and voice availability", caps.get("excel") and caps.get("import") and caps.get("voice_input"),
		  {k: caps.get(k) for k in ("excel", "import", "analytics", "documents", "knowledge", "voice_input", "voice_output")})

	# --- Excel report through a chat turn (worker + tool + file)
	r = c.post("chat.send_message", text="Please create an excel report of this year's sales by month")
	conv = r["conversation"]
	reply = wait_reply(c, conv, 0)
	blocks = {b["type"]: b for b in (reply or {}).get("blocks") or []}
	check("Excel report created by the worker", "file" in blocks, (reply or {}).get("status"))
	if "file" in blocks:
		dl = c.s.get(f"{BASE}{blocks['file']['url']}", headers=c.h)
		check("Private report downloads for its owner (valid XLSX)", dl.status_code == 200 and dl.content[:2] == b"PK", dl.status_code)
		anon = requests.get(f"{BASE}{blocks['file']['url']}", headers=c.h)
		check("Report is not downloadable without login", anon.status_code in (401, 403, 404), anon.status_code)

	# --- Knowledge: worker indexing + real FULLTEXT + citations
	up = c.upload("files.upload", "file", f"policy_{TAG}.txt",
				  f"LEAVE POLICY {TAG}\n\nEvery permanent employee is entitled to twelve days of casual leave {TAG}.".encode(),
				  "text/plain", conversation=conv)
	file_url = None
	frappe = fx()
	file_url = frappe.db.get_value("File", up["file"], "file_url")
	frappe.destroy()
	kd = c.s.post(f"{BASE}/api/resource/Chikku AI Knowledge Document",
				  json={"title": f"Leave Policy {TAG}", "file": file_url, "visibility": "Only Me", "category": "HR"},
				  headers={**c.h, "X-Frappe-CSRF-Token": c.csrf}).json().get("data", {})
	status = None
	for _ in range(40):
		time.sleep(1.5)
		status = c.s.get(f"{BASE}/api/resource/Chikku AI Knowledge Document/{kd.get('name')}", headers=c.h).json().get("data", {}).get("status")
		if status in ("Indexed", "Failed"):
			break
	check("Knowledge document indexed by the background worker", status == "Indexed", status)
	time.sleep(1)
	before = max(m["seq"] for m in c.get("chat.get_messages", conversation=conv)["messages"])
	c.post("chat.send_message", text="What does our leave policy say?", conversation=conv)
	reply = wait_reply(c, conv, before)
	cites = [b for b in (reply or {}).get("blocks") or [] if b["type"] == "citations"]
	check("Knowledge answer carries citations from FULLTEXT search", bool(cites) and cites[0]["items"][0]["title"] == f"Leave Policy {TAG}",
		  cites[:1])

	# --- Import studio end to end
	csv_text = ("Customer Name,Customer Group,Territory,Type,Company,Receivable Account\n"
				f"R2 Test One {TAG},{group},{terr},Company,{COMPANY},{acc}\n"
				f"R2 Test Two {TAG},{group},{terr},Individual,{COMPANY},{acc}\n"
				f"R2 Test Bad {TAG},No Such Group,{terr},Company,{COMPANY},{acc}\n")
	up = c.upload("files.upload", "file", f"customers_{TAG}.csv", csv_text.encode(), "text/csv", conversation=conv)
	before = max(m["seq"] for m in c.get("chat.get_messages", conversation=conv)["messages"])
	c.post("chat.send_message", text="Import these customers please", conversation=conv, attachments=json.dumps([up["file"]]))
	reply = wait_reply(c, conv, before)
	mb = next((b for b in (reply or {}).get("blocks") or [] if b["type"] == "import_mapping"), None)
	check("Import mapping editor returned", bool(mb), (reply or {}).get("status"))
	if mb:
		fix = {"Type": "customer_type", "Company": "accounts.company", "Receivable Account": "accounts.account"}
		mapping = [{**m, "field": fix.get(m["header"], m["field"])} for m in mb["columns"]]
		c.post("imports.update_mapping", session=mb["session"], mapping=json.dumps(mapping), defaults="{}")
		c.post("imports.validate", session=mb["session"])
		prev = None
		for _ in range(40):
			time.sleep(1.5)
			prev = c.get("imports.get_session", session=mb["session"])
			if prev.get("status") == "Validated":
				break
		check("Background validation: 2 valid, 1 invalid (unknown group)",
			  prev.get("valid_records") == 2 and prev.get("invalid_rows") == 1, {k: prev.get(k) for k in ("status", "valid_records", "invalid_rows")})
		req = c.post("imports.request_confirmation", session=mb["session"])
		res = c.post("actions.confirm", action_request=req["action_request"], token=req["token"])
		check("Import confirmed with single-use token", res.get("ok"), res)
		final = None
		for _ in range(60):
			time.sleep(2)
			final = c.get("imports.history")
			row = next((x for x in final if x["name"] == mb["session"]), {})
			if row.get("status") in ("Completed", "Partially Completed", "Failed"):
				break
		check("Native Data Import ran in the worker as the user", row.get("status") == "Completed" and row.get("success_count") == 2, row)
		frappe = fx()
		di = frappe.db.get_value("Chikku AI Import Session", mb["session"], "data_import")
		dio = frappe.db.get_value("Data Import", di, ["owner", "submit_after_import", "status"], as_dict=True) if di else {}
		n = frappe.db.count("Customer", {"customer_name": ["like", f"R2 Test % {TAG}"]})
		frappe.destroy()
		check("Data Import owned by the user, never submitting", dio.get("owner") == USER and dio.get("submit_after_import") == 0, dio)
		check("Exactly the 2 valid customers exist", n == 2, n)
		replay = c.post("actions.confirm", action_request=req["action_request"], token=req["token"])
		check("Replaying the import token is rejected", replay.get("ok") is False, replay)

	# --- Voice: consent, upload, worker STT via Sarvam protocol, realtime transcript
	no_consent = c.upload("voice.transcribe", "audio", "v.webm", b"\x1aE\xdf\xa3audio", "audio/webm")
	check("Voice requires consent first", "_error" in no_consent, no_consent.get("_error"))
	c.post("voice.give_consent")
	v = c.upload("voice.transcribe", "audio", "v.webm", b"\x1aE\xdf\xa3audio-bytes", "audio/webm")
	time.sleep(6)
	got = [json.loads(e) for e in events if '"transcript"' in e]
	check("Transcript delivered over socket.io", got and got[-1]["data"].get("text") == "is mahine ki sales dikhao", got[-1:] if got else v)
	stt = [x for x in CALLS if x.get("path", "").endswith("speech-to-text")]
	check("Speech service received the audio with its key", stt and stt[-1]["key"] == "local-test-key", stt[-1:])

	# --- Activity Centre API + page
	act = c.get("activity.list_activity")
	kinds = {r["kind"] for r in act.get("rows", [])}
	check("Activity Centre lists reports, imports and knowledge", {"Report / Excel", "Import", "Knowledge"} <= kinds, sorted(kinds))
	page = c.s.get(f"{BASE}/api/method/frappe.desk.desk_page.getpage", params={"name": "chikku-activity"}, headers=c.h)
	check("Activity Centre page loads for the user", page.status_code == 200 and "chikku-activity" in page.text, page.status_code)
	types = sorted({json.loads(e)["data"].get("type") for e in events if '"chikku_ai_event"' in e})
	check("Realtime events received", {"status", "tool", "done"} <= set(types), types)
	listener.kill()


def main():
	server = ThreadingHTTPServer(("127.0.0.1", PORT), Mock)
	threading.Thread(target=server.serve_forever, daemon=True).start()
	saved, group, terr, acc = setup()
	try:
		run(group, terr, acc)
	finally:
		server.shutdown()
		cleanup(saved)
	print(f"SUMMARY {sum(RESULTS)} pass / {len(RESULTS) - sum(RESULTS)} fail", flush=True)


if __name__ == "__main__":
	main()
