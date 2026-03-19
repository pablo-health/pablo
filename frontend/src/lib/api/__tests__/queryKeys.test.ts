// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Query Key Structure Tests
 *
 * Ensures query keys are structured correctly for cache invalidation.
 */

import { describe, it, expect } from "vitest"
import { queryKeys } from "../queryKeys"

describe("Query Keys", () => {
  describe("patients", () => {
    it("generates base key", () => {
      expect(queryKeys.patients.all).toEqual(["patients"])
    })

    it("generates lists key", () => {
      expect(queryKeys.patients.lists()).toEqual(["patients", "list"])
    })

    it("generates list key without params", () => {
      expect(queryKeys.patients.list()).toEqual([
        "patients",
        "list",
        undefined,
      ])
    })

    it("generates list key with search param", () => {
      expect(queryKeys.patients.list({ search: "Smith" })).toEqual([
        "patients",
        "list",
        { search: "Smith" },
      ])
    })

    it("generates list key with full params", () => {
      expect(
        queryKeys.patients.list({ search: "Smith", search_by: "last_name" })
      ).toEqual([
        "patients",
        "list",
        { search: "Smith", search_by: "last_name" },
      ])
    })

    it("generates different keys for different search params", () => {
      const key1 = queryKeys.patients.list({ search: "Smith" })
      const key2 = queryKeys.patients.list({ search: "Jones" })

      expect(key1).not.toEqual(key2)
    })

    it("generates different keys for different search_by params", () => {
      const key1 = queryKeys.patients.list({
        search: "Jane",
        search_by: "first_name",
      })
      const key2 = queryKeys.patients.list({
        search: "Jane",
        search_by: "last_name",
      })

      expect(key1).not.toEqual(key2)
    })

    it("generates details key", () => {
      expect(queryKeys.patients.details()).toEqual(["patients", "detail"])
    })

    it("generates detail key for specific patient", () => {
      expect(queryKeys.patients.detail("patient-123")).toEqual([
        "patients",
        "detail",
        "patient-123",
      ])
    })

    it("generates different detail keys for different patients", () => {
      const key1 = queryKeys.patients.detail("patient-123")
      const key2 = queryKeys.patients.detail("patient-456")

      expect(key1).not.toEqual(key2)
    })

    it("hierarchical structure allows partial invalidation", () => {
      // Base key matches all patient queries
      const baseKey = queryKeys.patients.all
      const listKey = queryKeys.patients.list({ search: "Smith" })
      const detailKey = queryKeys.patients.detail("patient-123")

      // List and detail keys should start with base key
      expect(listKey[0]).toEqual(baseKey[0])
      expect(detailKey[0]).toEqual(baseKey[0])
    })

    it("list keys without params differ from list keys with params", () => {
      const withoutParams = queryKeys.patients.list()
      const withParams = queryKeys.patients.list({ search: "Smith" })

      expect(withoutParams).not.toEqual(withParams)
    })
  })

  describe("sessions", () => {
    it("generates base key", () => {
      expect(queryKeys.sessions.all).toEqual(["sessions"])
    })

    it("generates lists key", () => {
      expect(queryKeys.sessions.lists()).toEqual(["sessions", "list"])
    })

    it("generates list key", () => {
      expect(queryKeys.sessions.list()).toEqual(["sessions", "list"])
    })

    it("generates details key", () => {
      expect(queryKeys.sessions.details()).toEqual(["sessions", "detail"])
    })

    it("generates detail key for specific session", () => {
      expect(queryKeys.sessions.detail("session-123")).toEqual([
        "sessions",
        "detail",
        "session-123",
      ])
    })

    it("generates different detail keys for different sessions", () => {
      const key1 = queryKeys.sessions.detail("session-123")
      const key2 = queryKeys.sessions.detail("session-456")

      expect(key1).not.toEqual(key2)
    })

    it("generates byPatient key", () => {
      expect(queryKeys.sessions.byPatient("patient-123")).toEqual([
        "sessions",
        "byPatient",
        "patient-123",
      ])
    })

    it("generates different byPatient keys for different patients", () => {
      const key1 = queryKeys.sessions.byPatient("patient-123")
      const key2 = queryKeys.sessions.byPatient("patient-456")

      expect(key1).not.toEqual(key2)
    })

    it("hierarchical structure allows partial invalidation", () => {
      // Base key matches all session queries
      const baseKey = queryKeys.sessions.all
      const listKey = queryKeys.sessions.list()
      const detailKey = queryKeys.sessions.detail("session-123")
      const byPatientKey = queryKeys.sessions.byPatient("patient-123")

      // All keys should start with base key
      expect(listKey[0]).toEqual(baseKey[0])
      expect(detailKey[0]).toEqual(baseKey[0])
      expect(byPatientKey[0]).toEqual(baseKey[0])
    })
  })

  describe("type safety", () => {
    it("keys are readonly (as const)", () => {
      // This test verifies TypeScript types compile correctly
      // If keys weren't 'as const', this would be a type error
      const baseKey: readonly ["patients"] = queryKeys.patients.all
      expect(baseKey).toEqual(["patients"])
    })

    it("patient list params are type-safe", () => {
      // Valid search_by values
      queryKeys.patients.list({ search: "Smith", search_by: "first_name" })
      queryKeys.patients.list({ search: "Smith", search_by: "last_name" })

      // TypeScript would error on invalid values:
      // queryKeys.patients.list({ search: "Smith", search_by: "invalid" })
    })
  })

  describe("cache invalidation patterns", () => {
    it("invalidating all patients affects lists and details", () => {
      const baseKey = queryKeys.patients.all
      const listKey = queryKeys.patients.list({ search: "Smith" })
      const detailKey = queryKeys.patients.detail("patient-123")

      // Both should start with base key
      expect(listKey[0]).toEqual(baseKey[0])
      expect(detailKey[0]).toEqual(baseKey[0])
    })

    it("invalidating lists affects all search variations", () => {
      const listsKey = queryKeys.patients.lists()
      const list1 = queryKeys.patients.list({ search: "Smith" })
      const list2 = queryKeys.patients.list({ search: "Jones" })

      // Both lists should start with lists key
      expect(list1.slice(0, 2)).toEqual(listsKey)
      expect(list2.slice(0, 2)).toEqual(listsKey)
    })

    it("invalidating specific detail does not affect other details", () => {
      const detail1 = queryKeys.patients.detail("patient-123")
      const detail2 = queryKeys.patients.detail("patient-456")

      // Different patient IDs create different keys
      expect(detail1).not.toEqual(detail2)
    })
  })
})
