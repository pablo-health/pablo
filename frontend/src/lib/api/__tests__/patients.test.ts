// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient API Function Tests
 *
 * Tests that API functions call the client correctly with proper endpoints and payloads.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import * as client from "../client"
import {
  createPatient,
  listPatients,
  getPatient,
  updatePatient,
  deletePatient,
} from "../patients"
import { createMockPatient } from "@/test/factories"

vi.mock("../client")

describe("Patient API Functions", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("createPatient", () => {
    it("calls post with correct endpoint and data", async () => {
      const mockPatient = createMockPatient({
        date_of_birth: "1985-03-15",
        diagnosis: "Anxiety",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      })

      vi.mocked(client.post).mockResolvedValue(mockPatient)

      const data = {
        first_name: "Jane",
        last_name: "Doe",
        date_of_birth: "1985-03-15",
        diagnosis: "Anxiety",
      }

      const result = await createPatient(data)

      expect(client.post).toHaveBeenCalledWith("/api/patients", data, undefined)
      expect(result).toEqual(mockPatient)
    })

    it("passes token when provided", async () => {
      const mockPatient = createMockPatient({
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      })

      vi.mocked(client.post).mockResolvedValue(mockPatient)

      await createPatient(
        { first_name: "Jane", last_name: "Doe" },
        "test-token"
      )

      expect(client.post).toHaveBeenCalledWith(
        "/api/patients",
        expect.any(Object),
        "test-token"
      )
    })

    it("creates patient with minimal data", async () => {
      const mockPatient = createMockPatient({
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      })

      vi.mocked(client.post).mockResolvedValue(mockPatient)

      const minimalData = {
        first_name: "Jane",
        last_name: "Doe",
      }

      await createPatient(minimalData)

      expect(client.post).toHaveBeenCalledWith(
        "/api/patients",
        minimalData,
        undefined
      )
    })
  })

  describe("listPatients", () => {
    it("calls get without query string when no params", async () => {
      const mockResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(client.get).mockResolvedValue(mockResponse)

      await listPatients()

      expect(client.get).toHaveBeenCalledWith("/api/patients", undefined)
    })

    it("builds query string correctly for search by last name", async () => {
      const mockResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(client.get).mockResolvedValue(mockResponse)

      await listPatients({ search: "Smith", search_by: "last_name" })

      expect(client.get).toHaveBeenCalledWith(
        "/api/patients?search=Smith&search_by=last_name",
        undefined
      )
    })

    it("builds query string correctly for search by first name", async () => {
      const mockResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(client.get).mockResolvedValue(mockResponse)

      await listPatients({ search: "Jane", search_by: "first_name" })

      expect(client.get).toHaveBeenCalledWith(
        "/api/patients?search=Jane&search_by=first_name",
        undefined
      )
    })

    it("builds query string with only search term", async () => {
      const mockResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(client.get).mockResolvedValue(mockResponse)

      await listPatients({ search: "Smith" })

      expect(client.get).toHaveBeenCalledWith(
        "/api/patients?search=Smith",
        undefined
      )
    })

    it("passes token when provided", async () => {
      const mockResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(client.get).mockResolvedValue(mockResponse)

      await listPatients(undefined, "test-token")

      expect(client.get).toHaveBeenCalledWith("/api/patients", "test-token")
    })
  })

  describe("getPatient", () => {
    it("calls get with correct endpoint", async () => {
      const mockPatient = createMockPatient({
        session_count: 5,
        last_session_date: "2024-01-01T00:00:00Z",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      })

      vi.mocked(client.get).mockResolvedValue(mockPatient)

      const result = await getPatient("patient-123")

      expect(client.get).toHaveBeenCalledWith(
        "/api/patients/patient-123",
        undefined
      )
      expect(result).toEqual(mockPatient)
    })

    it("passes token when provided", async () => {
      const mockPatient = createMockPatient({
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      })

      vi.mocked(client.get).mockResolvedValue(mockPatient)

      await getPatient("patient-123", "test-token")

      expect(client.get).toHaveBeenCalledWith(
        "/api/patients/patient-123",
        "test-token"
      )
    })
  })

  describe("updatePatient", () => {
    it("calls patch with correct endpoint and data", async () => {
      const mockPatient = createMockPatient({
        diagnosis: "Generalized Anxiety Disorder",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-02T00:00:00Z",
      })

      vi.mocked(client.patch).mockResolvedValue(mockPatient)

      const updateData = { diagnosis: "Generalized Anxiety Disorder" }
      const result = await updatePatient("patient-123", updateData)

      expect(client.patch).toHaveBeenCalledWith(
        "/api/patients/patient-123",
        updateData,
        undefined
      )
      expect(result).toEqual(mockPatient)
    })

    it("updates multiple fields", async () => {
      const mockPatient = createMockPatient({
        last_name: "Smith",
        date_of_birth: "1985-03-15",
        diagnosis: "GAD",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-02T00:00:00Z",
      })

      vi.mocked(client.patch).mockResolvedValue(mockPatient)

      const updateData = {
        last_name: "Smith",
        date_of_birth: "1985-03-15",
        diagnosis: "GAD",
      }

      await updatePatient("patient-123", updateData)

      expect(client.patch).toHaveBeenCalledWith(
        "/api/patients/patient-123",
        updateData,
        undefined
      )
    })

    it("passes token when provided", async () => {
      const mockPatient = createMockPatient({
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      })

      vi.mocked(client.patch).mockResolvedValue(mockPatient)

      await updatePatient("patient-123", { diagnosis: "GAD" }, "test-token")

      expect(client.patch).toHaveBeenCalledWith(
        "/api/patients/patient-123",
        { diagnosis: "GAD" },
        "test-token"
      )
    })
  })

  describe("deletePatient", () => {
    it("calls del with correct endpoint", async () => {
      const mockResponse = {
        message: "Patient and 5 sessions deleted successfully",
      }

      vi.mocked(client.del).mockResolvedValue(mockResponse)

      const result = await deletePatient("patient-123")

      expect(client.del).toHaveBeenCalledWith(
        "/api/patients/patient-123",
        undefined
      )
      expect(result).toEqual(mockResponse)
    })

    it("passes token when provided", async () => {
      const mockResponse = {
        message: "Patient deleted successfully",
      }

      vi.mocked(client.del).mockResolvedValue(mockResponse)

      await deletePatient("patient-123", "test-token")

      expect(client.del).toHaveBeenCalledWith(
        "/api/patients/patient-123",
        "test-token"
      )
    })
  })
})
