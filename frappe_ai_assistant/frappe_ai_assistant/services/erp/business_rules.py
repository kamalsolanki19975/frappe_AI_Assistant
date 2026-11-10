"""Amba Garments rules that exist only in browser scripts and must also hold for Chikku drafts.

Inventory of enabled Client Scripts (checked 2026-11-03 on ambatest.local):
- restrict_backdate.js (app_include_js, Sales Invoice form): posting/transaction date must not be
  earlier than today - 3 days unless the user has role "Backdate Approval". -> enforced here for
  every Chikku draft (settings.backdate_days / backdate_bypass_role).
- Client Script "Sales Invoice": due date >= posting date; posting date change limited after 4 days
  (submit-time). Sales Invoice drafts are not enabled in Chikku yet.
- Client Script "Purchase Order" (35 kB): approver/responder fields, BOM price comparison. PO drafts
  are not enabled in Chikku yet; the server script "Purchase Order" (Before Insert) still applies.
- Client Scripts "Production Plan", "Stock Entry": form helpers (fetch items/work order); not
  relevant to enabled drafts.
- Client Script "Sales Order": disabled.
These rules are enforced *inside Chikku only*. Changing them for all channels needs separate approval.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today


class BusinessRuleError(frappe.ValidationError):
	pass


def check_backdate(settings, value, label: str = "Date"):
	if not value:
		return
	days = cint(settings.backdate_days)
	if days <= 0:
		return
	bypass = settings.backdate_bypass_role
	if bypass and bypass in frappe.get_roles():
		return
	earliest = add_days(getdate(today()), -days)
	if getdate(value) < earliest:
		raise BusinessRuleError(
			_("{0} cannot be earlier than {1} (backdate limit of {2} days).").format(
				label, frappe.utils.formatdate(earliest), days
			)
		)
