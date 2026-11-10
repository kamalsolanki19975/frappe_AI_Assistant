"""Personal greeting shown when the panel opens on an empty conversation.

The first name always comes from the User record of the session user, never from the client.
Templates are plain str.replace (no format/Jinja), so admin-edited text cannot run code.
"""

import frappe
from frappe.utils import cstr

FALLBACK = {"en": "there", "hi": "जी", "hinglish": "ji"}


def first_name(user: str | None = None) -> str | None:
	user = user or frappe.session.user
	row = frappe.db.get_value("User", user, ["first_name", "full_name"], as_dict=True) or {}
	name = cstr(row.get("first_name")).strip() or cstr(row.get("full_name")).strip().split(" ")[0]
	return name[:40] or None


def build(settings, language: str, user: str | None = None) -> str:
	name = first_name(user)
	template = {
		"hi": settings.welcome_message_hi,
		"hinglish": settings.welcome_message_hinglish,
	}.get(language) or settings.welcome_message or "Hello {first_name}! How can I help you today?"
	text = cstr(template)
	if name:
		text = text.replace("{first_name}", name)
	else:
		# "Hey {first_name}!" -> "Hey there!" without leaving a stray placeholder
		text = text.replace("{first_name}", FALLBACK.get(language, "there"))
	return text.replace("{assistant_name}", cstr(settings.assistant_name or "Chikku AI"))[:600]
