"""Strip secrets and cap sizes before anything is written to logs."""

import re

SECRET_KEY_RE = re.compile(r"(api[_-]?key|secret|password|passwd|token|authorization|auth|cookie|sid)", re.I)
SECRET_VALUE_RE = re.compile(
	r"(sk-[A-Za-z0-9_\-]{12,}|sk-ant-[A-Za-z0-9_\-]{12,}|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+[A-Za-z0-9._\-]{12,}|"
	r"x-api-key:\s*\S+)"
)
MAX_STR = 300
MAX_ITEMS = 25


def redact_text(text: str) -> str:
	if not isinstance(text, str):
		return text
	return SECRET_VALUE_RE.sub("[REDACTED]", text)


def redact(value, depth: int = 0):
	if depth > 4:
		return "…"
	if isinstance(value, dict):
		out = {}
		for i, (k, v) in enumerate(value.items()):
			if i >= MAX_ITEMS:
				out["…"] = f"{len(value) - MAX_ITEMS} more keys"
				break
			out[k] = "[REDACTED]" if SECRET_KEY_RE.search(str(k)) else redact(v, depth + 1)
		return out
	if isinstance(value, (list, tuple)):
		items = [redact(v, depth + 1) for v in list(value)[:MAX_ITEMS]]
		if len(value) > MAX_ITEMS:
			items.append(f"… {len(value) - MAX_ITEMS} more")
		return items
	if isinstance(value, str):
		value = redact_text(value)
		return value if len(value) <= MAX_STR else value[:MAX_STR] + "…"
	return value
