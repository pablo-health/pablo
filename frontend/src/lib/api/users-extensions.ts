// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Extension slot for the user API module.
 *
 * Ships empty here. A downstream build (e.g. a deployment overlay) may
 * overwrite *this file only* to:
 *   - widen `UserStatus` / `UpdateUserRequest` (declare object types for the
 *     `*Extensions` aliases below), and
 *   - add extra user-API types and functions.
 *
 * `users.ts` intersects these into its exported types and re-exports
 * everything here (`export *`), so consumers keep importing from
 * `@/lib/api/users` and base additions in `users.ts` never require the
 * downstream build to re-declare it.
 *
 * `unknown` is the identity for intersection (`T & unknown` is `T`), so with
 * the empty defaults `UserStatus` / `UpdateUserRequest` are exactly their base
 * shapes. A downstream build replaces these with object types to add fields.
 */
export type UserStatusExtensions = unknown
export type UpdateUserRequestExtensions = unknown
