// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Finds hook calls that the React Compiler puts behind its memo cache.
 *
 * The app builds with the React Compiler. It caches values it considers
 * stable in a per-component array and recomputes them only when their inputs
 * change, by wrapping the computation in `if ($[n] === …)`. A hook call that
 * ends up inside such a block runs on the first render and is skipped on
 * later ones, so React sees fewer hooks than before and the component crashes
 * ("Rendered fewer hooks than expected", minified error #300).
 *
 * Source that does this can look correct and pass lint and every unit test,
 * since tests run without the compiler. The usual cause is a hook written as
 * part of a larger expression the compiler caches, such as an object
 * literal: `const refs = { a: useRef(null) }`. The compiler's lint rules do
 * not report it either. Compiling the file and reading the output does.
 */

import { readdirSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"
import { parseSync, transformSync } from "@babel/core"
import reactCompiler from "babel-plugin-react-compiler"

export interface CachedHookCall {
  file: string
  hook: string
}

type Node = { type?: string; [key: string]: unknown }

const HOOK_NAME = /^use[A-Z]/

function isNode(value: unknown): value is Node {
  return typeof value === "object" && value !== null && typeof (value as Node).type === "string"
}

function children(node: Node): Node[] {
  return Object.entries(node).flatMap(([key, value]) => {
    if (key === "loc" || key === "start" || key === "end") return []
    if (Array.isArray(value)) return value.filter(isNode)
    return isNode(value) ? [value] : []
  })
}

/** True for a test that reads the compiler's cache: `$[n] === …` and the like. */
function readsCache(node: Node): boolean {
  if (
    node.type === "MemberExpression" &&
    isNode(node.object) &&
    node.object.type === "Identifier" &&
    node.object.name === "$"
  ) {
    return true
  }
  return children(node).some(readsCache)
}

function hookName(call: Node): string | null {
  const callee = call.callee
  if (!isNode(callee)) return null
  if (callee.type === "Identifier" && HOOK_NAME.test(String(callee.name))) return String(callee.name)
  if (
    callee.type === "MemberExpression" &&
    isNode(callee.property) &&
    callee.property.type === "Identifier" &&
    HOOK_NAME.test(String(callee.property.name))
  ) {
    return String(callee.property.name)
  }
  return null
}

function collect(node: Node, inCache: boolean, found: string[]): void {
  // A nested function is not run as part of this render.
  if (
    node.type === "FunctionExpression" ||
    node.type === "ArrowFunctionExpression" ||
    node.type === "FunctionDeclaration"
  ) {
    if (inCache) return
  }
  if (inCache && node.type === "CallExpression") {
    const name = hookName(node)
    if (name) found.push(name)
  }
  if (node.type === "IfStatement" && isNode(node.test) && readsCache(node.test)) {
    collect(node.test, inCache, found)
    if (isNode(node.consequent)) collect(node.consequent, true, found)
    if (isNode(node.alternate)) collect(node.alternate, true, found)
    return
  }
  for (const child of children(node)) collect(child, inCache, found)
}

/** Hook calls in `source` that the compiled output only runs on some renders. */
export function cachedHookCalls(source: string, filename: string): string[] {
  const compiled = transformSync(source, {
    filename,
    babelrc: false,
    configFile: false,
    parserOpts: { plugins: ["typescript", "jsx"] },
    plugins: [[reactCompiler, {}]],
  })?.code
  if (!compiled) return []
  const ast = parseSync(compiled, {
    filename,
    babelrc: false,
    configFile: false,
    parserOpts: { plugins: ["typescript", "jsx"] },
  })
  if (!ast) return []
  const found: string[] = []
  collect(ast as unknown as Node, false, found) // Babel's File node is a plain object tree; the walker only reads `type` and child nodes
  return found
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (name === "node_modules" || name === "__tests__" || name.startsWith(".")) return []
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.tsx?$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name) && !name.endsWith(".d.ts")
      ? [path]
      : []
  })
}

/** Every cached hook call in the TypeScript sources under `dirs`. */
export function findCachedHookCalls(dirs: string[]): CachedHookCall[] {
  return dirs.flatMap((dir) =>
    sourceFiles(dir).flatMap((file) =>
      cachedHookCalls(readFileSync(file, "utf-8"), file).map((hook) => ({ file, hook })),
    ),
  )
}
