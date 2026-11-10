"""Module status and the service contracts modules follow.

A module implements its Protocol, registers fixed tools under its capability flag
(tools/module_tools.py) and reuses security/tokens.py for any write.
"""

from __future__ import annotations

from typing import Protocol

STATUS = {
	# capability flag in Chikku AI Settings -> implementation status (R2, 2026-11-04)
	"chat_enabled": "implemented",
	"erp_context_enabled": "implemented",
	"erp_read_tools_enabled": "implemented",
	"draft_actions_enabled": "implemented (Sales Order; Purchase Invoice from Purchase Receipt/Order)",
	"attachments_enabled": "implemented",
	"excel_enabled": "implemented (excel/)",
	"import_enabled": "implemented (imports/)",
	"analytics_enabled": "implemented (analytics/)",
	"document_intelligence_enabled": "implemented (documents/); AI/vision extraction needs a configured model",
	"knowledge_enabled": "implemented (knowledge/); embedding re-rank needs an Embedding model",
	"voice_enabled": "implemented (voice/); needs Speech Recognition / Speech Synthesis providers",
	"image_processing_enabled": "implemented through document intelligence (vision model required)",
	"workflow_intelligence_enabled": "implemented (workflows/)",
	"notifications_enabled": "implemented (notifications/)",
}


class ExcelService(Protocol):
	"""Planned: XlsxWriter for generation, openpyxl (read_only, data_only) for reading."""

	def inspect(self, file_name: str) -> dict: ...
	def generate(self, spec: dict) -> str: ...  # returns private File name
	def modify(self, file_name: str, operations: list[dict]) -> str: ...  # always a new file


class ImportService(Protocol):
	"""Planned: native Data Import as the engine; submit_after_import forced to 0 and guarded."""

	def eligible_doctypes(self) -> list[dict]: ...
	def propose_mapping(self, file_name: str, doctype: str) -> dict: ...
	def preview(self, session: str) -> dict: ...
	def start(self, session: str, token: str) -> dict: ...


class DocumentIntelligenceService(Protocol):
	def extract(self, file_name: str, doc_type_hint: str | None = None) -> dict: ...


class AnalyticsService(Protocol):
	"""Planned: vetted metric registry with deterministic calculations; no model-written SQL."""

	def metric(self, metric_id: str, period: dict, company: str, **kwargs) -> dict: ...


class KnowledgeService(Protocol):
	"""Planned: MariaDB FULLTEXT + embedding re-rank, collection ACL checked per query."""

	def search(self, query: str, collection: str | None = None) -> list[dict]: ...


class VoiceService(Protocol):
	"""Planned: STT/TTS through provider slots (Sarvam is the leading Indic candidate)."""

	def transcribe(self, file_name: str, language_hint: str | None = None) -> str: ...
	def synthesize(self, text: str, language: str) -> str: ...


class WorkflowProposalService(Protocol):
	"""Planned: proposals saved disabled; an administrator activates them in native UI."""

	def propose(self, requirement: str) -> dict: ...
