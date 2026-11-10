"""Who may use Chikku AI, and the settings object everything reads."""

import frappe
from frappe import _

from frappe_ai_assistant.frappe_ai_assistant import CONVERSATION, MANAGER_ROLE, SETTINGS, USER_ROLE

ACCESS_ROLES = {USER_ROLE, MANAGER_ROLE, "System Manager"}
ADMIN_ROLES = {MANAGER_ROLE, "System Manager"}


class ChikkuAccessError(frappe.PermissionError):
	pass


def get_settings():
	return frappe.get_cached_doc(SETTINGS)


def has_access(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if user == "Administrator":
		return True
	if frappe.db.get_value("User", user, "user_type") != "System User":
		return False
	return bool(ACCESS_ROLES.intersection(frappe.get_roles(user)))


def is_admin(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(ADMIN_ROLES.intersection(frappe.get_roles(user)))


def capability_allowed(settings, capability: str, user: str | None = None) -> bool:
	"""Feature Availability by Role: if a capability has role rows, only those roles may use it."""
	rows = [r.role for r in (settings.get("capability_roles") or []) if r.capability == capability]
	if not rows:
		return True
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(rows) & set(frappe.get_roles(user)))


def require_access(capability: str | None = None):
	"""Raise unless the session user may use Chikku AI (and the capability is on)."""
	settings = get_settings()
	if not settings.enabled:
		frappe.throw(_("Chikku AI is currently disabled by your administrator."), ChikkuAccessError)
	if not has_access():
		frappe.throw(_("You do not have access to Chikku AI."), ChikkuAccessError)
	if capability and (not settings.get(capability) or not capability_allowed(settings, capability)):
		frappe.throw(_("This Chikku AI feature is turned off for you by your administrator."), ChikkuAccessError)
	return settings


def require_admin():
	if not is_admin():
		frappe.throw(_("Only Chikku AI Managers can do this."), ChikkuAccessError)


def get_owned_conversation(name: str, for_update: bool = False):
	"""Load a conversation only if the session user owns it. Same error for missing and foreign."""
	if not name or not isinstance(name, str):
		frappe.throw(_("Conversation not found."), frappe.DoesNotExistError)
	row = frappe.db.get_value(
		CONVERSATION, name, ["name", "user", "status"], as_dict=True, for_update=for_update
	)
	if not row or row.user != frappe.session.user:
		if row:
			from frappe_ai_assistant.frappe_ai_assistant.audit.logger import audit

			audit("ownership_violation", severity="High", conversation=None,
				  details={"conversation": name, "owner_mismatch": True})
		frappe.throw(_("Conversation not found."), frappe.DoesNotExistError)
	return row
