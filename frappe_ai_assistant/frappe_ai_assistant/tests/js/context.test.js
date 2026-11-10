import assert from "node:assert/strict";
import { test } from "node:test";
import { getDeskContext } from "../../../public/js/chikku_ai/lib/context.js";

const env = (route, extra = {}) => ({ frappe: { get_route: () => route }, ...extra });

test("form context uses the open document", () => {
	const ctx = getDeskContext(env(["Form", "Sales Order", "SO-1"], { cur_frm: { doctype: "Sales Order", docname: "SO-1", is_new: () => false } }));
	assert.equal(ctx.kind, "form");
	assert.equal(ctx.docname, "SO-1");
	assert.equal(ctx.label, "Sales Order SO-1");
});

test("unsaved new form sends no document name", () => {
	const ctx = getDeskContext(env(["Form", "Sales Order", "new-sales-order-1"], { cur_frm: { doctype: "Sales Order", docname: "new-sales-order-1", is_new: () => true } }));
	assert.equal(ctx.docname, null);
});

test("list context caps selected records at 50", () => {
	const names = Array.from({ length: 80 }, (_, i) => `SO-${i}`);
	const ctx = getDeskContext(env(["List", "Sales Order"], { cur_list: { doctype: "Sales Order", get_checked_items: () => [...names, { bad: 1 }] } }));
	assert.equal(ctx.kind, "list");
	assert.equal(ctx.selected.length, 50);
	assert.match(ctx.label, /50 selected/);
});

test("workspace and broken routes give no context", () => {
	assert.equal(getDeskContext(env(["Workspaces", "Home"])), null);
	assert.equal(getDeskContext({ frappe: { get_route: () => { throw new Error("x"); } } }), null);
});
