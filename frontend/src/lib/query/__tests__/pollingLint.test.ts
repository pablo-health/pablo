// @vitest-environment node
// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Lint refuses a bare `refetchInterval` anywhere but pollingOptions, so a new
 * poller cannot quietly send polls that count as activity and keep an idle
 * session signed in. Runs the project's real ESLint config over fixtures.
 */
import path from "node:path"
import { ESLint } from "eslint"
import { describe, expect, it } from "vitest"

const FRONTEND_ROOT = path.resolve(__dirname, "../../../..")
const RULE_MESSAGE = /pollingOptions/

async function pollingErrors(code: string, filePath: string): Promise<string[]> {
  const eslint = new ESLint({ cwd: FRONTEND_ROOT })
  const [result] = await eslint.lintText(code, { filePath: path.join(FRONTEND_ROOT, filePath) })
  return result.messages
    .filter((m) => m.ruleId === "no-restricted-syntax" && RULE_MESSAGE.test(m.message))
    .map((m) => m.message)
}

const BARE = `export const options = { queryKey: ["k"], refetchInterval: 60_000 }\n`
const QUOTED = `export const options = { queryKey: ["k"], "refetchInterval": 60_000 }\n`
const SHORTHAND = `const refetchInterval = 1000\nexport const options = { refetchInterval }\n`
const WRAPPED = [
  `import { pollingOptions } from "@/lib/query/polling"`,
  `export const options = pollingOptions({ queryKey: ["k"], queryFn: () => 1 }, 60_000)`,
  ``,
].join("\n")

describe("lint: polling goes through pollingOptions", () => {
  it.each([
    ["a hook", "src/hooks/useExample.ts", BARE],
    ["a page", "app/(dashboard)/dashboard/example/page.tsx", BARE],
    ["src/lib", "src/lib/example.ts", BARE],
    ["a test", "src/hooks/__tests__/example.test.ts", BARE],
    ["a quoted key", "src/hooks/useExample.ts", QUOTED],
    ["shorthand", "src/hooks/useExample.ts", SHORTHAND],
  ])("rejects a bare refetchInterval in %s", async (_where, filePath, code) => {
    expect(await pollingErrors(code, filePath)).toHaveLength(1)
  }, 30_000)

  it("accepts pollingOptions", async () => {
    expect(await pollingErrors(WRAPPED, "src/hooks/useExample.ts")).toEqual([])
  }, 30_000)

  it("allows refetchInterval inside pollingOptions itself", async () => {
    expect(await pollingErrors(BARE, "src/lib/query/polling.ts")).toEqual([])
  }, 30_000)
})
