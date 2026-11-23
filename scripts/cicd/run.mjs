import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const { values } = parseArgs({
  options: {
    python: { type: "string", multiple: true },
    help: { type: "boolean" },
  },
});
if (values.help) {
  console.log("npm run ci:full [-- --python 3.10 --python 3.12]");
  console.log("Default: both Python versions; dependencies, lint, offline tests, build, then live data-source health checks once.");
  process.exit(0);
}

const versions = [...new Set(values.python ?? ["3.10", "3.12"])];
if (versions.some((version) => !["3.10", "3.12"].includes(version))) {
  console.error("Choose a CI Python version: 3.10 or 3.12.");
  process.exit(1);
}

const root = fileURLToPath(new URL("../../", import.meta.url));
const results = [];
const started = performance.now();
for (const version of versions) {
  const reportDir = join(root, "reports", "ci", `python-${version}`);
  mkdirSync(reportDir, { recursive: true });
  const env = {
    ...process.env,
    PYTHONUTF8: "1",
    UV_PROJECT_ENVIRONMENT: join(root, ".venv-ci", `python-${version}`),
  };
  const run = ["run", "--frozen", "--python", version];
  const stages = [
    ["dependencies", ["sync", "--frozen", "--dev", "--python", version]],
    ["lint", [...run, "ruff", "check", "--select", "E9,F63,F7,F82", "simtradedata", "scripts", "tests"]],
    ["tests", [
      ...run, "pytest", "tests/", "--disable-socket",
      "-m", "not external and not performance",
      `--junitxml=${join(reportDir, "pytest.xml")}`,
      "--cov=simtradedata",
      `--cov-report=xml:${join(reportDir, "coverage.xml")}`,
    ]],
    ["build", ["build", "--no-sources", "--python", version,
      "--out-dir", join(root, "dist", "ci", `python-${version}`)]],
  ];
  if (version === versions.at(-1)) {
    stages.push(
      ["health:data-sources", [...run, "python", "-m", "scripts.check_data_sources",
        "--timeout", "90", "--attempts", "2", "--json-output", join(reportDir, "data-sources.json")]],
      ["health:mootdx-interfaces", [...run, "python", "-m", "scripts.check_mootdx_interfaces",
        "--timeout", "90", "--json-output", join(reportDir, "mootdx-interfaces.json")]],
      ["health:etf-pcf", [...run, "python", "-m", "scripts.check_etf_pcf",
        "--archive-dir", join(reportDir, "etf-pcf"), "--json-output", join(reportDir, "etf-pcf.json")]],
    );
  }
  let dependenciesReady = true;
  for (const [stage, args] of stages) {
    if (!dependenciesReady) {
      results.push({ python: version, stage, status: "skipped", elapsed_seconds: 0 });
      continue;
    }
    console.log(`\n==> Python ${version}: ${stage}`);
    const stepStarted = performance.now();
    const child = spawnSync("uv", args, { cwd: root, env, stdio: "inherit" });
    if (child.error) console.error(child.error.message);
    const exitCode = child.status ?? 1;
    const result = {
      python: version,
      stage,
      status: exitCode === 0 ? "passed" : "failed",
      exit_code: exitCode,
      elapsed_seconds: Number(((performance.now() - stepStarted) / 1000).toFixed(3)),
    };
    results.push(result);
    if (stage === "dependencies") dependenciesReady = exitCode === 0;
  }
}

const ok = results.every((result) => result.status === "passed");
const report = {
  ok,
  elapsed_seconds: Number(((performance.now() - started) / 1000).toFixed(3)),
  results,
};
const summaryPath = join(root, "reports", "ci", "summary.json");
writeFileSync(summaryPath, JSON.stringify(report, null, 2) + "\n");
console.log("\nCI summary:");
for (const result of results) {
  console.log(`  Python ${result.python} ${result.stage}: ${result.status} (${result.elapsed_seconds}s)`);
}
console.log(`Report: ${summaryPath}`);
process.exitCode = ok ? 0 : 1;
