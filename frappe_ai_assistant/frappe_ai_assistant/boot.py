import frappe


def extend_bootinfo(bootinfo):
	"""Only what the launcher needs. No secrets, no provider details."""
	try:
		from frappe_ai_assistant.frappe_ai_assistant.security.access import get_settings, has_access

		settings = get_settings()
		if not settings.enabled or not settings.chat_enabled or not has_access():
			return
		bootinfo.chikku_ai = {
			"enabled": 1,
			"assistant_name": settings.assistant_name or "Chikku AI",
			"avatar": settings.avatar,
		}
	except Exception:
		# The Desk must load even if Chikku AI is broken.
		frappe.log_error(title="Chikku AI boot failed")
