"""The fixed tool registry: the only way the model can reach ERP data.

Each tool declares a name, description, pydantic input model (extra keys forbidden),
kind (read / draft), the settings capability it needs, and an audit policy.
`run_tool` is the guard: unknown tool -> rejected, capability off -> rejected,
schema invalid -> rejected, then the tool runs as the session user.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import frappe
from pydantic import BaseModel, ValidationError

from frappe_ai_assistant.frappe_ai_assistant.logger import audit, log_tool_call
from frappe_ai_assistant.frappe_ai_assistant.providers.base import ToolSpec
from frappe_ai_assistant.frappe_ai_assistant.services.erp.policy import PolicyError


@dataclass
class Tool:
	name: str
	description: str
	input_model: type[BaseModel]
	handler: Callable
	kind: str = "read"  # read | draft
	capability: str = "erp_read_tools_enabled"
	audit: str = "call"

	def spec(self) -> ToolSpec:
		schema = inline_refs(self.input_model.model_json_schema())
		return ToolSpec(name=self.name, description=self.description, parameters=schema)


def inline_refs(schema: dict) -> dict:
	"""Resolve $ref/$defs and drop titles so every provider accepts the schema."""
	defs = schema.pop("$defs", {})

	def walk(node):
		if isinstance(node, dict):
			if "$ref" in node:
				target = defs[node["$ref"].split("/")[-1]]
				return walk({**target, **{k: v for k, v in node.items() if k != "$ref"}})
			return {k: walk(v) for k, v in node.items() if k != "title"}
		if isinstance(node, list):
			return [walk(v) for v in node]
		return node

	out = walk(schema)
	out.setdefault("type", "object")
	out.setdefault("properties", {})
	return out


@dataclass
class ToolContext:
	user: str
	settings: object
	conversation: str | None = None
	message: str | None = None
	context: dict = field(default_factory=dict)
	language: str = "en"
	# Collected for the UI (never sent back to the model as instructions)
	blocks: list = field(default_factory=list)
	action_tokens: list = field(default_factory=list)


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool):
	if tool.name in REGISTRY:
		raise ValueError(f"duplicate tool {tool.name}")
	REGISTRY[tool.name] = tool
	return tool


def _load():
	from . import erp_tools, module_tools  # noqa: F401  (registers tools)


def available_tools(settings) -> list[Tool]:
	from frappe_ai_assistant.frappe_ai_assistant.security.access import capability_allowed

	_load()
	return [t for t in REGISTRY.values() if settings.get(t.capability) and capability_allowed(settings, t.capability)]


def _summarise_args(args: dict) -> dict:
	return {k: v for k, v in (args or {}).items()}


def _row_count(result) -> int:
	if not isinstance(result, dict):
		return 0
	for key in ("row_count", "rows"):
		v = result.get(key)
		if isinstance(v, bool):
			continue
		if isinstance(v, int):
			return v
		if isinstance(v, (list, tuple)):
			return len(v)
	return 0


def _safe_errors():
	"""Module exceptions whose messages are written for users."""
	from frappe_ai_assistant.frappe_ai_assistant.documents.extract import DocumentError
	from frappe_ai_assistant.frappe_ai_assistant.excel.modify import ModifyError
	from frappe_ai_assistant.frappe_ai_assistant.excel.reader import SpreadsheetError

	return (DocumentError, ModifyError, SpreadsheetError)


def run_tool(name: str, args: dict, ctx: ToolContext) -> dict:
	"""Execute one tool call. Always returns a JSON-serialisable dict (errors included)."""
	from frappe_ai_assistant.frappe_ai_assistant.security.access import capability_allowed

	_load()

	started = time.monotonic()
	tool = REGISTRY.get(name)
	status, error_code, rows = "OK", None, 0

	def done(result):
		log_tool_call(tool_name=name, kind=tool.kind if tool else "read", status=status, args=_summarise_args(args),
					  user=ctx.user, conversation=ctx.conversation, message=ctx.message, error_code=error_code,
					  rows_returned=rows, duration_ms=int((time.monotonic() - started) * 1000))
		return result

	if frappe.session.user != ctx.user:
		# Never run a tool under a different identity than the turn's owner.
		status, error_code = "Denied", "IDENTITY_MISMATCH"
		audit("tool_identity_mismatch", "Critical", user=ctx.user, conversation=ctx.conversation,
			  details={"tool": name, "session_user": frappe.session.user})
		return done({"error": "DENIED", "message": "Tool execution refused."})

	if not tool:
		status, error_code = "Denied", "UNKNOWN_TOOL"
		audit("unknown_tool_requested", "Warning", user=ctx.user, conversation=ctx.conversation,
			  details={"tool": str(name)[:80]})
		return done({"error": "UNKNOWN_TOOL", "message": f"There is no tool named '{str(name)[:60]}'."})

	if not ctx.settings.get(tool.capability) or not capability_allowed(ctx.settings, tool.capability):
		status, error_code = "Denied", "CAPABILITY_DISABLED"
		return done({"error": "CAPABILITY_DISABLED", "message": "This capability is turned off by the administrator."})

	try:
		parsed = tool.input_model.model_validate(args or {})
	except ValidationError as e:
		status, error_code = "Denied", "INVALID_ARGS"
		problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
		return done({"error": "INVALID_ARGS", "message": problems[:500]})

	savepoint = f"chikku_tool_{int(time.monotonic() * 1000)}"
	blocks_before, tokens_before = len(ctx.blocks), len(ctx.action_tokens)

	def discard_ui():
		# A failed tool must not leave cards (e.g. a download link to a rolled-back file) or tokens behind.
		del ctx.blocks[blocks_before:]
		del ctx.action_tokens[tokens_before:]

	try:
		frappe.db.savepoint(savepoint)
		result = tool.handler(parsed, ctx)
		rows = _row_count(result)
		return done(result)
	except (PolicyError, frappe.PermissionError, frappe.DoesNotExistError) as e:
		frappe.db.rollback(save_point=savepoint)
		discard_ui()
		status = "Denied"
		msg = str(e) or "NOT_FOUND_OR_NO_ACCESS"
		error_code = msg.split(":")[0][:60] if msg.isupper() or ":" in msg else "NOT_FOUND_OR_NO_ACCESS"
		if isinstance(e, frappe.PermissionError) or "NO_ACCESS" in msg:
			audit("permission_denied", "Info", user=ctx.user, conversation=ctx.conversation,
				  details={"tool": name})
		frappe.clear_messages()
		return done({"error": error_code, "message": msg[:500]})
	except (frappe.ValidationError, *_safe_errors()) as e:
		frappe.db.rollback(save_point=savepoint)
		discard_ui()
		status, error_code = "Error", "VALIDATION"
		frappe.clear_messages()
		return done({"error": "VALIDATION", "message": frappe.utils.strip_html(str(e))[:500]})
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		discard_ui()
		status, error_code = "Error", "INTERNAL"
		frappe.log_error(title=f"Chikku AI tool {name} failed")
		frappe.clear_messages()
		return done({"error": "INTERNAL", "message": "The tool failed unexpectedly. The error was logged."})
