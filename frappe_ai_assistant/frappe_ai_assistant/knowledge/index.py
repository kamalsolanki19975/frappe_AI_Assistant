"""Company Knowledge: ingestion and permission-filtered retrieval.

Retrieval = MariaDB FULLTEXT (InnoDB, natural language mode) over chunks of documents the user may read,
optionally re-ranked by embedding cosine similarity when an Embedding model slot is configured. No extra
service: MariaDB 10.11 has no vector type, and the corpus size fits brute-force re-ranking of top candidates.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

import frappe
from frappe.utils import cint, cstr, now_datetime

from frappe_ai_assistant.frappe_ai_assistant.documents.extract import DocumentError, extract_text
from frappe_ai_assistant.frappe_ai_assistant.providers import router
from frappe_ai_assistant.frappe_ai_assistant.providers.base import ProviderError

DOC = "Chikku AI Knowledge Document"
CHUNK = "Chikku AI Knowledge Chunk"
ALLOWED_EXT = {"pdf", "docx", "txt", "md", "csv", "xlsx"}
MAX_CHARS = 2_000_000
HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+[A-Z].{2,80}|[A-Z][A-Z0-9 &/,'-]{3,80}|#{1,4}\s+.{2,80})\s*$")


def ensure_fulltext_index():
	table = f"tab{CHUNK}"
	if not frappe.db.table_exists(CHUNK):
		return
	existing = frappe.db.sql(f"show index from `{table}` where Key_name='chikku_ft_content'")
	if not existing:
		frappe.db.sql_ddl(f"alter table `{table}` add fulltext index chikku_ft_content (content)")


def _file_content(doc) -> tuple[bytes, str]:
	f = frappe.db.get_value("File", {"file_url": doc.file}, ["name", "file_name"], as_dict=True)
	if not f:
		raise DocumentError("The attached file could not be found.")
	fdoc = frappe.get_doc("File", f.name)
	if not fdoc.has_permission("read"):
		raise DocumentError("You can't access the attached file.")
	from frappe_ai_assistant.frappe_ai_assistant.services.files import read_bytes

	return read_bytes(fdoc), f.file_name


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[dict]:
	"""Paragraph-aware chunks that remember the nearest heading and page marker."""
	chunks, buf, heading, page = [], "", None, None
	for para in re.split(r"\n\s*\n|\n(?=\[Page \d+\])", text):
		para = para.strip()
		if not para:
			continue
		m = re.match(r"\[Page (\d+)\]\s*", para)
		if m:
			page = int(m.group(1))
			para = para[m.end():].strip()
			if not para:
				continue
		first_line = para.split("\n", 1)[0]
		if HEADING_RE.match(first_line) and len(first_line) < 90:
			heading = first_line.strip("# ").strip()
		while len(para) > size:
			cut = para.rfind(". ", 0, size)
			cut = cut + 1 if cut > size // 2 else size
			if buf:
				chunks.append({"content": buf.strip(), "heading": heading, "page": page})
				buf = ""
			chunks.append({"content": para[:cut].strip(), "heading": heading, "page": page})
			para = para[max(cut - overlap, 0):].strip()
		if len(buf) + len(para) + 2 > size and buf:
			chunks.append({"content": buf.strip(), "heading": heading, "page": page})
			buf = buf[-overlap:] if overlap else ""
		buf = f"{buf}\n\n{para}" if buf else para
	if buf.strip():
		chunks.append({"content": buf.strip(), "heading": heading, "page": page})
	return chunks


def index_document(name: str):
	"""Background job (runs as the document owner/editor)."""
	doc = frappe.get_doc(DOC, name)
	if not doc.has_permission("write"):
		return
	settings = frappe.get_cached_doc("Chikku AI Settings")
	doc.db_set({"status": "Indexing", "error": None})
	frappe.db.commit()
	try:
		ext = (doc.file or "").rsplit(".", 1)[-1].lower()
		if ext not in ALLOWED_EXT:
			raise DocumentError(f"Unsupported file type .{ext}. Use PDF, DOCX, TXT, MD, CSV or XLSX.")
		content, file_name = _file_content(doc)
		checksum = hashlib.sha256(content).hexdigest()
		data = extract_text(content, file_name)
		text = data["text"][:MAX_CHARS]
		if len(text.strip()) < 20:
			raise DocumentError("No readable text found (scanned documents need OCR before they can be indexed).")
		chunks = chunk_text(text, cint(settings.knowledge_chunk_chars) or 1200)
		vectors, model = _embed([c["content"] for c in chunks], settings)
		frappe.db.delete(CHUNK, {"knowledge_document": name})
		for i, c in enumerate(chunks):
			frappe.get_doc({"doctype": CHUNK, "knowledge_document": name, "seq": i + 1, "heading": (c["heading"] or "")[:200],
							"page": c["page"], "content": c["content"],
							"embedding": json.dumps([round(x, 5) for x in vectors[i]]) if vectors else None}
						   ).db_insert()  # code-only index table; access is enforced on the parent document
		doc.db_set({"status": "Indexed", "chunk_count": len(chunks), "char_count": len(text), "checksum": checksum,
					"indexed_on": now_datetime(), "embedding_model": model, "error": None})
		frappe.db.commit()
	except (DocumentError, ProviderError) as e:
		frappe.db.rollback()
		frappe.db.set_value(DOC, name, {"status": "Failed", "error": str(e)[:500]})
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Chikku AI knowledge indexing failed")
		frappe.db.set_value(DOC, name, {"status": "Failed", "error": "Unexpected error; see Error Log."})
		frappe.db.commit()


def _embed(texts: list[str], settings) -> tuple[list[list[float]] | None, str | None]:
	cfg = router.optional_slot(settings, "embedding")
	if not cfg or not texts:
		return None, None
	adapter = router.get_adapter(cfg)
	if not hasattr(adapter, "embed"):
		return None, None
	out = []
	for i in range(0, len(texts), 64):
		out += adapter.embed(texts[i:i + 64])
	return out, cfg.model


def _cosine(a, b):
	dot = sum(x * y for x, y in zip(a, b))
	na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
	return dot / (na * nb) if na and nb else 0.0


def search(query: str, *, category: str | None = None, limit: int = 6) -> dict:
	"""Only documents the session user may read are searched (permission filter BEFORE retrieval)."""
	query = cstr(query).strip()[:300]
	if not query:
		return {"results": [], "searched_documents": 0}
	if not frappe.has_permission(DOC, "read"):
		return {"results": [], "searched_documents": 0, "note": "No indexed knowledge documents are available to you."}
	filters = {"status": "Indexed"}
	if category:
		filters["category"] = category
	docs = frappe.get_list(DOC, filters=filters, fields=["name", "title", "category", "file"], limit_page_length=500)
	if not docs:
		return {"results": [], "searched_documents": 0, "note": "No indexed knowledge documents are available to you."}
	allowed = {d.name: d for d in docs}
	names = list(allowed)
	rows = frappe.db.sql(
		f"""select name, knowledge_document, heading, page, content, embedding,
		match(content) against (%(q)s in natural language mode) as score
		from `tab{CHUNK}` where knowledge_document in %(docs)s
		and match(content) against (%(q)s in natural language mode) > 0
		order by score desc limit 40""",
		{"q": query, "docs": names}, as_dict=True)
	if not rows:
		words = [w for w in re.findall(r"\w+", query, re.U) if len(w) >= 3][:6]
		if words:
			cond = " or ".join(["content like %s"] * len(words))
			rows = frappe.db.sql(
				f"""select name, knowledge_document, heading, page, content, embedding, 0.1 as score
				from `tab{CHUNK}` where knowledge_document in %s and ({cond}) limit 40""",
				[tuple(names), *[f"%{w}%" for w in words]], as_dict=True)
	settings = frappe.get_cached_doc("Chikku AI Settings")
	if rows and any(r.embedding for r in rows):
		try:
			qv, _m = _embed([query], settings)
			if qv:
				for r in rows:
					if r.embedding:
						r.score = 0.4 * float(r.score or 0) / (max(float(x.score or 0) for x in rows) or 1) + \
							0.6 * _cosine(qv[0], json.loads(r.embedding))
				rows.sort(key=lambda r: r.score, reverse=True)
		except ProviderError:
			pass
	results = []
	for i, r in enumerate(rows[: cint(limit) or 6], start=1):
		d = allowed[r.knowledge_document]
		results.append({"n": i, "document": d.name, "title": d.title, "category": d.category, "heading": r.heading,
						"page": r.page, "text": r.content[:1500], "score": round(float(r.score or 0), 3)})
	return {"results": results, "searched_documents": len(names)}
