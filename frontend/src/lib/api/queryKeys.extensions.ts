// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Extension slot for the query-key factory.
 *
 * Ships empty here. A downstream build (e.g. a deployment overlay) may
 * overwrite *this file only* to register additional query keys; `queryKeys.ts`
 * deep-merges them per namespace at module load. That means adding keys to the
 * base factory in `queryKeys.ts` never requires a downstream build to
 * re-declare existing keys — the merge slot is the single, stable seam.
 *
 * Shape: a (partial) mirror of the `queryKeys` structure — namespaces of
 * readonly tuples and/or key-factory functions. New top-level namespaces and
 * new sub-keys within an existing namespace are both supported; leaves
 * (tuples / functions) replace, objects merge.
 */
export const queryKeyExtensions = {} as const
