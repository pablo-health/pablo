// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * No component's hooks end up behind the React Compiler's memo cache.
 *
 * See `src/test/compiledHooks.ts` for why this needs the compiler's output
 * rather than a lint rule. The first two cases pin the checker itself, so a
 * clean result for the app means "nothing to find", not "cannot see".
 */

import { join } from "node:path"
import { describe, expect, it } from "vitest"
import { cachedHookCalls, findCachedHookCalls } from "@/test/compiledHooks"

const FRONTEND = join(__dirname, "..", "..", "..")

describe("hooks under the React Compiler", () => {
  // The shape the compiler caches: an object of refs, read in handlers and
  // passed as props. A ref read during render would make the compiler skip
  // the component, and there would be nothing to find.
  const card = (refs: string) => `
    import { useRef, useState } from "react"
    export function Card({ items }) {
      const [missing, setMissing] = useState(null)
      ${refs}
      const submit = () => {
        if (!missing) return
        firstOf[missing].current?.focus()
      }
      return (
        <div>
          {items.map((d, i) => <input key={d} ref={i === 0 ? firstOf.days : undefined} />)}
          <button onClick={submit}>Go</button>
          <button onClick={() => setMissing("days")}>Check</button>
        </div>
      )
    }
  `

  it("flags a hook written inside an object literal", () => {
    const source = card("const firstOf = { days: useRef(null), times: useRef(null) }")
    expect(cachedHookCalls(source, "Card.tsx")).toEqual(["useRef", "useRef"])
  })

  it("passes the same hooks called at the top level", () => {
    const source = card(`
      const days = useRef(null)
      const times = useRef(null)
      const firstOf = { days, times }
    `)
    expect(cachedHookCalls(source, "Card.tsx")).toEqual([])
  })

  it(
    "finds none in the app",
    () => {
      const found = findCachedHookCalls([join(FRONTEND, "src"), join(FRONTEND, "app")])
      expect(found.map(({ file, hook }) => `${file.slice(FRONTEND.length + 1)}: ${hook}`)).toEqual([])
    },
    180_000,
  )
})
