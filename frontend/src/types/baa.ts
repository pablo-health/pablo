/**
 * BAA types.
 *
 * The contract lives in `baa.extensions.ts` — a single-file slot a downstream
 * build overwrites to supply the real shapes and any form types. This file is
 * a stable re-export shim so shared modules import from `@/types/baa` without
 * ever forking it. See `baa.extensions.ts` for the stub/extension contract.
 */

export * from "./baa.extensions"
