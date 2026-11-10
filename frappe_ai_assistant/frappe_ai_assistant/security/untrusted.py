"""Untrusted content (ERP text, tool results, attachments) is passed to the model as data only."""

import json
import re

INJECTION_PATTERNS = re.compile(
	r"(ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (the )?(system|previous)|"
	r"you are now|new instructions:|system prompt|<\s*/?\s*system\s*>|act as (an? )?administrator|"
	r"call the tool|confirm (the )?action|reveal (your|the) (prompt|key|instructions))",
	re.I,
)


def looks_like_injection(text: str) -> bool:
	return bool(text and INJECTION_PATTERNS.search(text))


def _neutralise(text: str) -> str:
	# The model must not be able to close our data wrappers from inside the data.
	return re.sub(r"</?\s*(untrusted_data|tool_result|erp_context)\b", "[tag]", text, flags=re.I)


def wrap(kind: str, payload) -> str:
	body = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
	return f'<untrusted_data kind="{kind}">\n{_neutralise(body)}\n</untrusted_data>'
