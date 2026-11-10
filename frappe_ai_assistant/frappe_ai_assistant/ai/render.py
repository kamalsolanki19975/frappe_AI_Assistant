"""Markdown -> safe HTML. Raw HTML from the model is escaped first, then nh3 enforces an allow-list."""

import markdown2
import nh3

ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "code", "pre", "ul", "ol", "li", "blockquote", "hr",
				"h3", "h4", "h5", "table", "thead", "tbody", "tr", "th", "td", "a", "span", "del"}
ALLOWED_ATTRS = {"a": {"href", "title"}, "th": {"align"}, "td": {"align"}}


def to_html(text: str) -> str:
	if not text:
		return ""
	html = markdown2.markdown(
		text,
		safe_mode="escape",
		extras=["tables", "fenced-code-blocks", "cuddled-lists", "break-on-newline", "strike"],
	)
	return nh3.clean(
		html,
		tags=ALLOWED_TAGS,
		attributes=ALLOWED_ATTRS,
		url_schemes={"http", "https"},
		link_rel="noopener noreferrer nofollow",
	)
