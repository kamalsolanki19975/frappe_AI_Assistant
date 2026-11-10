"""Runs the Chikku AI test-suite against the current site inside ONE transaction and rolls it back.

Usage:
	bench --site <site> execute frappe_ai_assistant.frappe_ai_assistant.tests.runner.run

Why not `bench run-tests`? It creates permanent `_Test ...` fixture records (companies, items,
customers) in the database. This site holds real data, so the suite instead creates its own
throw-away users/records and leaves nothing behind.
"""

import io
import json
import os
import sys
import unittest

import frappe

MODULES = [
	"frappe_ai_assistant.frappe_ai_assistant.tests.test_providers",
	"frappe_ai_assistant.frappe_ai_assistant.tests.test_core",
	"frappe_ai_assistant.frappe_ai_assistant.tests.test_tools_actions",
	"frappe_ai_assistant.frappe_ai_assistant.tests.test_r2_modules",
]


def run(modules: str | None = None, verbosity: int = 2):
	frappe.flags.chikku_ai_sync_audit = True
	frappe.flags.mute_emails = True
	frappe.set_user("Administrator")
	names = modules.split(",") if modules else MODULES
	suite = unittest.TestSuite()
	loader = unittest.TestLoader()
	for name in names:
		suite.addTests(loader.loadTestsFromName(name))
	stream = io.StringIO()
	result = unittest.TextTestRunner(stream=stream, verbosity=int(verbosity)).run(suite)
	frappe.db.rollback()
	for u in frappe.get_all("User", {"name": ["like", "%@chikku-test.invalid"]}, pluck="name"):
		frappe.clear_cache(user=u)
	leftover = frappe.db.count("User", {"name": ["like", "%@chikku-test.invalid"]})
	frappe.flags.chikku_ai_sync_audit = False
	output = stream.getvalue()
	print(output)
	summary = {
		"ran": result.testsRun,
		"failures": len(result.failures),
		"errors": len(result.errors),
		"skipped": len(result.skipped),
		"ok": result.wasSuccessful(),
		"test_users_left_after_rollback": leftover,
	}
	print("SUMMARY " + json.dumps(summary))
	sys.stdout.flush()
	return summary
