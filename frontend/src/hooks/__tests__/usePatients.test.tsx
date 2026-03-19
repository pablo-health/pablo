// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient React Query Hook Tests
 *
 * Tests hooks with real QueryClient, mock API functions.
 * Includes optimistic update and cache invalidation tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  usePatientList,
  usePatient,
  useCreatePatient,
  useUpdatePatient,
  useDeletePatient,
} from "../usePatients"
import * as patientsApi from "@/lib/api/patients"
import type { PatientResponse } from "@/types/patients"
import { createMockPatient } from "@/test/factories"

vi.mock("@/lib/api/patients")

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

const mockPatient: PatientResponse = createMockPatient({
  date_of_birth: "1985-03-15",
  diagnosis: "Anxiety",
  session_count: 5,
  last_session_date: "2024-01-01T00:00:00Z",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
})

describe("usePatients hooks", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("usePatientList", () => {
    it("fetches patient list successfully", async () => {
      const mockData = {
        data: [mockPatient],
        total: 1,
        page: 1,
        page_size: 50,
      }

      vi.mocked(patientsApi.listPatients).mockResolvedValue(mockData)

      const { result } = renderHook(() => usePatientList(), {
        wrapper: createWrapper(),
      })

      expect(result.current.isLoading).toBe(true)

      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(result.current.data).toEqual(mockData)
      expect(patientsApi.listPatients).toHaveBeenCalledWith(undefined, undefined)
    })

    it("passes search params to API", async () => {
      const mockData = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(patientsApi.listPatients).mockResolvedValue(mockData)

      const params = { search: "Smith", search_by: "last_name" as const }
      renderHook(() => usePatientList(params), {
        wrapper: createWrapper(),
      })

      await waitFor(() =>
        expect(patientsApi.listPatients).toHaveBeenCalledWith(params, undefined)
      )
    })

    it("passes token when provided", async () => {
      const mockData = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(patientsApi.listPatients).mockResolvedValue(mockData)

      renderHook(() => usePatientList(undefined, "test-token"), {
        wrapper: createWrapper(),
      })

      await waitFor(() =>
        expect(patientsApi.listPatients).toHaveBeenCalledWith(
          undefined,
          "test-token"
        )
      )
    })

    it("handles API errors", async () => {
      const error = new Error("API Error")
      vi.mocked(patientsApi.listPatients).mockRejectedValue(error)

      const { result } = renderHook(() => usePatientList(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => expect(result.current.isError).toBe(true))

      expect(result.current.error).toEqual(error)
    })
  })

  describe("usePatient", () => {
    it("fetches single patient successfully", async () => {
      vi.mocked(patientsApi.getPatient).mockResolvedValue(mockPatient)

      const { result } = renderHook(() => usePatient("patient-123"), {
        wrapper: createWrapper(),
      })

      expect(result.current.isLoading).toBe(true)

      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(result.current.data).toEqual(mockPatient)
      expect(patientsApi.getPatient).toHaveBeenCalledWith(
        "patient-123",
        undefined
      )
    })

    it("respects enabled option", async () => {
      vi.mocked(patientsApi.getPatient).mockResolvedValue(mockPatient)

      const { result } = renderHook(
        () => usePatient("patient-123", undefined, { enabled: false }),
        {
          wrapper: createWrapper(),
        }
      )

      // Should not fetch if disabled
      expect(result.current.isLoading).toBe(false)
      expect(patientsApi.getPatient).not.toHaveBeenCalled()
    })

    it("passes token when provided", async () => {
      vi.mocked(patientsApi.getPatient).mockResolvedValue(mockPatient)

      renderHook(() => usePatient("patient-123", "test-token"), {
        wrapper: createWrapper(),
      })

      await waitFor(() =>
        expect(patientsApi.getPatient).toHaveBeenCalledWith(
          "patient-123",
          "test-token"
        )
      )
    })
  })

  describe("useCreatePatient", () => {
    it("creates patient and invalidates queries", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      const newPatient: PatientResponse = {
        ...mockPatient,
        id: "patient-new",
      }

      vi.mocked(patientsApi.createPatient).mockResolvedValue(newPatient)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useCreatePatient(), { wrapper })

      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        first_name: "Jane",
        last_name: "Doe",
      })

      expect(patientsApi.createPatient).toHaveBeenCalled()
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["patients", "list"],
      })
    })

    it("returns created patient data", async () => {
      const newPatient: PatientResponse = {
        ...mockPatient,
        id: "patient-new",
      }

      vi.mocked(patientsApi.createPatient).mockResolvedValue(newPatient)

      const { result } = renderHook(() => useCreatePatient(), {
        wrapper: createWrapper(),
      })

      const created = await result.current.mutateAsync({
        first_name: "Jane",
        last_name: "Doe",
        date_of_birth: "1985-03-15",
        diagnosis: "Anxiety",
      })

      expect(created).toEqual(newPatient)
    })

    it("handles creation errors", async () => {
      const error = new Error("Creation failed")
      vi.mocked(patientsApi.createPatient).mockRejectedValue(error)

      const { result } = renderHook(() => useCreatePatient(), {
        wrapper: createWrapper(),
      })

      await expect(
        result.current.mutateAsync({
          first_name: "Jane",
          last_name: "Doe",
        })
      ).rejects.toThrow("Creation failed")
    })
  })

  describe("useUpdatePatient", () => {
    it("updates patient with optimistic update", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      // Pre-populate cache
      queryClient.setQueryData(["patients", "detail", "patient-123"], mockPatient)

      const updatedPatient: PatientResponse = {
        ...mockPatient,
        diagnosis: "Generalized Anxiety Disorder",
      }

      vi.mocked(patientsApi.updatePatient).mockResolvedValue(updatedPatient)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useUpdatePatient(), { wrapper })

      await result.current.mutateAsync({
        patientId: "patient-123",
        data: { diagnosis: "Generalized Anxiety Disorder" },
      })

      // Should have updated cache
      const cachedData = queryClient.getQueryData<PatientResponse>([
        "patients",
        "detail",
        "patient-123",
      ])
      expect(cachedData?.diagnosis).toBe("Generalized Anxiety Disorder")
    })

    it("rolls back optimistic update on error", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      // Pre-populate cache with original data
      queryClient.setQueryData(["patients", "detail", "patient-123"], mockPatient)

      const error = new Error("Update failed")
      vi.mocked(patientsApi.updatePatient).mockRejectedValue(error)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useUpdatePatient(), { wrapper })

      try {
        await result.current.mutateAsync({
          patientId: "patient-123",
          data: { diagnosis: "GAD" },
        })
      } catch {
        // Expected error
      }

      await waitFor(() => {
        // Should roll back to original data
        const cachedData = queryClient.getQueryData<PatientResponse>([
          "patients",
          "detail",
          "patient-123",
        ])
        expect(cachedData).toEqual(mockPatient)
      })
    })

    it("invalidates patient lists and detail on success", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      queryClient.setQueryData(["patients", "detail", "patient-123"], mockPatient)

      const updatedPatient: PatientResponse = {
        ...mockPatient,
        diagnosis: "GAD",
      }

      vi.mocked(patientsApi.updatePatient).mockResolvedValue(updatedPatient)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useUpdatePatient(), { wrapper })

      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        patientId: "patient-123",
        data: { diagnosis: "GAD" },
      })

      // Should invalidate detail
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["patients", "detail", "patient-123"],
      })

      // Should invalidate lists
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["patients", "list"],
      })
    })
  })

  describe("useDeletePatient", () => {
    it("removes patient from cache and invalidates queries", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      // Pre-populate cache
      queryClient.setQueryData(["patients", "detail", "patient-123"], mockPatient)

      const deleteResponse = {
        message: "Patient and 5 sessions deleted successfully",
      }

      vi.mocked(patientsApi.deletePatient).mockResolvedValue(deleteResponse)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useDeletePatient(), { wrapper })

      const removeSpy = vi.spyOn(queryClient, "removeQueries")
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync("patient-123")

      // Should remove patient from cache
      expect(removeSpy).toHaveBeenCalledWith({
        queryKey: ["patients", "detail", "patient-123"],
      })

      // Should invalidate patient lists
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["patients", "list"],
      })

      // Should invalidate sessions (cascading delete)
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["sessions"],
      })
    })

    it("returns delete confirmation message", async () => {
      const deleteResponse = {
        message: "Patient and 3 sessions deleted successfully",
      }

      vi.mocked(patientsApi.deletePatient).mockResolvedValue(deleteResponse)

      const { result } = renderHook(() => useDeletePatient(), {
        wrapper: createWrapper(),
      })

      const response = await result.current.mutateAsync("patient-123")

      expect(response).toEqual(deleteResponse)
    })

    it("handles deletion errors", async () => {
      const error = new Error("Deletion failed")
      vi.mocked(patientsApi.deletePatient).mockRejectedValue(error)

      const { result } = renderHook(() => useDeletePatient(), {
        wrapper: createWrapper(),
      })

      await expect(result.current.mutateAsync("patient-123")).rejects.toThrow(
        "Deletion failed"
      )
    })
  })
})
