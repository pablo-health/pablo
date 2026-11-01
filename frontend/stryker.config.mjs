// Mutation testing: plants small bugs in pure logic and checks that a test
// fails for each. Run with `npm run test:mutation`; reports land in
// reports/mutation/ and the incremental cache in reports/stryker-incremental.json.
// Sandboxes sit one level deeper than frontend/, so the npm script links the
// repo paths that portal-host tests read by relative path (backend/,
// frontend/public/) into .stryker-tmp. It also pins TZ, which vitest's `env`
// does not reach in Stryker's workers.
/** @type {import('@stryker-mutator/api/core').PartialStrykerOptions} */
const config = {
  testRunner: "vitest",
  vitest: { configFile: "vitest.config.ts" },
  mutate: [
    "src/lib/**/*.ts",
    "!src/lib/api/**",
    "!src/lib/mockData.ts",
    "!src/**/__tests__/**",
    "!src/**/*.test.ts",
  ],
  coverageAnalysis: "perTest",
  incremental: true,
  incrementalFile: "reports/stryker-incremental.json",
  reporters: ["clear-text", "progress", "html", "json"],
  thresholds: { high: 80, low: 60, break: null },
  timeoutMS: 10000,
  // The dry run executes every related spec in one process to map coverage.
  dryRunTimeoutMinutes: 30,
  // Module-level mutants re-run every spec; they are ~13% of mutants but ~98% of runtime.
  ignoreStatic: true,
  tempDirName: ".stryker-tmp",
}

export default config
