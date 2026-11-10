"""Attachment upload. Files are private, attached to the user's own conversation, type-checked by
extension and content signature. Content analysis is not part of this release."""

from __future__ import annotations

import filetype
import frappe
from frappe import _
from frappe.utils import cint, cstr

from frappe_ai_assistant.frappe_ai_assistant import CONVERSATION
from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit
from frappe_ai_assistant.frappe_ai_assistant.security import limits
from frappe_ai_assistant.frappe_ai_assistant.security.access import get_owned_conversation, require_access

# extension -> accepted detected MIME types (None = text file, checked separately)
ALLOWED = {
	"pdf": {"application/pdf"},
	"png": {"image/png"},
	"jpg": {"image/jpeg"},
	"jpeg": {"image/jpeg"},
	"webp": {"image/webp"},
	"xlsx": {"application/zip", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
	"docx": {"application/zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
	"csv": None,
	"txt": None,
}


def check_file(file_name: str, content: bytes, max_mb: int) -> str:
	name = cstr(file_name).strip().replace("\\", "/").split("/")[-1][:140]
	if not name or "." not in name:
		frappe.throw(_("Unsupported file type."))
	ext = name.rsplit(".", 1)[1].lower()
	if ext not in ALLOWED:
		frappe.throw(_("Unsupported file type: .{0}").format(ext[:10]))
	if not content:
		frappe.throw(_("The file is empty."))
	if len(content) > max_mb * 1024 * 1024:
		frappe.throw(_("The file is larger than {0} MB.").format(max_mb))
	expected = ALLOWED[ext]
	if expected is None:
		if b"\x00" in content[:4096]:
			frappe.throw(_("This doesn't look like a text file."))
		try:
			content[:65536].decode("utf-8")
		except UnicodeDecodeError:
			try:
				content[:65536].decode("cp1252")
			except UnicodeDecodeError:
				frappe.throw(_("This doesn't look like a text file."))
	else:
		kind = filetype.guess(content[:8192])
		if not kind or kind.mime not in expected:
			frappe.throw(_("The file content does not match its .{0} extension.").format(ext))
		if ext in ("xlsx", "docx") and (b"vbaProject.bin" in content):
			frappe.throw(_("Files with macros are not allowed."))
	return name


@frappe.whitelist(methods=["POST"])
def upload(conversation: str | None = None) -> dict:
	settings = require_access("attachments_enabled")
	limits.check_simple_rate("upload", 30, 3600)
	if conversation:
		get_owned_conversation(conversation)
	else:
		from .chat import _new_conversation

		conversation = _new_conversation()
	uploaded = frappe.request.files.get("file") if frappe.request and frappe.request.files else None
	if not uploaded:
		frappe.throw(_("No file received."))
	content = uploaded.stream.read()
	try:
		name = check_file(uploaded.filename, content, cint(settings.max_upload_mb) or 10)
	except frappe.ValidationError:
		audit("file_rejected", "Warning", conversation=None,
			  details={"file_name": cstr(uploaded.filename)[:100], "size": len(content)}, durable=True)
		raise
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": name,
		"content": content,
		"is_private": 1,
		"attached_to_doctype": CONVERSATION,
		"attached_to_name": conversation,
	})
	f.insert(ignore_permissions=True)  # attached to a conversation the user owns (checked above)
	return {"conversation": conversation, "file": f.name, "file_name": f.file_name,
			"kind": name.rsplit(".", 1)[1].lower(), "size_kb": round(len(content) / 1024, 1)}
