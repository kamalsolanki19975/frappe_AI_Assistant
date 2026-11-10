"""Attachment access and generated-file storage shared by all modules.

- Inputs: only files attached to a conversation owned by the session user.
- Outputs: private File records attached to the user's conversation (or to a job), so Frappe's
  own /private/files permission check only lets the owner download them.
"""

from __future__ import annotations

import hashlib
import re

import frappe
from frappe import _
from frappe.utils import cstr

from frappe_ai_assistant.frappe_ai_assistant import CONVERSATION


class FileAccessError(frappe.ValidationError):
	pass


def owned_attachment(file_name: str, conversation: str | None = None) -> "frappe.model.document.Document":
	"""Load a File the session user may use as Chikku input. Same error for missing and foreign."""
	f = frappe.db.get_value(
		"File", cstr(file_name)[:140],
		["name", "owner", "attached_to_doctype", "attached_to_name", "is_private", "file_name", "file_url"],
		as_dict=True,
	) if file_name else None
	ok = bool(f) and f.owner == frappe.session.user and f.is_private
	if ok and f.attached_to_doctype == CONVERSATION:
		ok = frappe.db.get_value(CONVERSATION, f.attached_to_name, "user") == frappe.session.user
		if ok and conversation:
			ok = f.attached_to_name == conversation
	elif ok and f.attached_to_doctype not in ("Chikku AI Job", "Chikku AI Import Session", "Chikku AI Knowledge Document"):
		ok = False
	if not ok:
		raise FileAccessError(_("File not found or not accessible."))
	return frappe.get_doc("File", f.name)


def read_bytes(file_doc) -> bytes:
	"""Raw bytes from disk. (File.get_content() may return binary files decoded as text.)"""
	if not file_doc.is_remote_file:
		with open(file_doc.get_full_path(), "rb") as fh:
			return fh.read()
	content = file_doc.get_content()
	return content.encode() if isinstance(content, str) else content


def sha256(content: bytes) -> str:
	return hashlib.sha256(content).hexdigest()


def safe_filename(name: str, ext: str) -> str:
	base = re.sub(r"[^A-Za-z0-9 _.-]+", "", cstr(name)).strip().replace(" ", "_")[:80] or "chikku_output"
	if not base.lower().endswith("." + ext):
		base = f"{base}.{ext}"
	return base


def save_output(content: bytes, file_name: str, attached_to_doctype: str, attached_to_name: str) -> dict:
	"""Store generated content privately. The caller must own the record it attaches to."""
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"content": content,
		"is_private": 1,
		"attached_to_doctype": attached_to_doctype,
		"attached_to_name": attached_to_name,
	})
	f.flags.ignore_permissions = True  # attached to a record owned by the session user (checked by caller)
	f.insert(ignore_permissions=True)
	return {"file": f.name, "file_name": f.file_name, "url": f.file_url, "size_kb": round(len(content) / 1024, 1)}


def conversation_attachments(conversation: str) -> list[dict]:
	"""Files the user attached in this conversation (for the model to refer to by name)."""
	if not conversation:
		return []
	rows = frappe.get_all(  # Chikku conversation ownership is verified before this is called
		"File",
		filters={"attached_to_doctype": CONVERSATION, "attached_to_name": conversation, "owner": frappe.session.user},
		fields=["name", "file_name", "file_size", "creation"],
		order_by="creation asc",
		limit_page_length=50,
	)
	return [{"file": r.name, "file_name": r.file_name, "size_kb": round((r.file_size or 0) / 1024, 1)} for r in rows]
