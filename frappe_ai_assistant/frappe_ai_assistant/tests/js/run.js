// Bundles each *.test.js (including .vue components) with Frappe's own esbuild + Vue plugin,
// then runs them with Node's built-in test runner. No extra npm packages are needed.
// Usage (Node >= 20): node apps/amba_garments/amba_garments/chikku_ai/tests/js/run.js
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const bench = path.resolve(__dirname, "../../../../../..");
const frappeModules = path.join(bench, "apps/frappe/node_modules");
const esbuild = require(path.join(frappeModules, "esbuild"));
const vue = require(path.join(frappeModules, "esbuild-plugin-vue3"));

const outdir = fs.mkdtempSync(path.join(os.tmpdir(), "chikku-js-tests-"));
const entries = fs.readdirSync(__dirname).filter((f) => f.endsWith(".test.js")).map((f) => path.join(__dirname, f));

esbuild
	.build({
		entryPoints: entries,
		bundle: true,
		platform: "node",
		format: "cjs",
		outdir,
		nodePaths: [frappeModules],
		plugins: [vue()],
		define: { "process.env.NODE_ENV": '"test"', __VUE_OPTIONS_API__: "true", __VUE_PROD_DEVTOOLS__: "false" },
		logLevel: "warning",
	})
	.then(() => {
		const files = fs.readdirSync(outdir).filter((f) => f.endsWith(".js")).map((f) => path.join(outdir, f));
		const res = spawnSync(process.execPath, ["--test", "--test-reporter=spec", ...files], { stdio: "inherit" });
		fs.rmSync(outdir, { recursive: true, force: true });
		process.exit(res.status);
	})
	.catch((e) => {
		console.error(e);
		process.exit(1);
	});
