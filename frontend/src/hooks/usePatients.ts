// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient React Query Hooks
 *
 * Custom hooks for patient management using React Query.
 * Includes optimistic updates, cache invalidation, and error handling.
 */

"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  CreatePatientRequest,
  PatientListParams,
  PatientResponse,
  UpdatePatientRequest,
} from "@/types/patients"
import {
  createPatient,
  deletePatient,
  getPatient,
  listPatients,
  updatePatient,
} from "@/lib/api/patients"
import { queryKeys } from "@/lib/api/queryKeys"

// ============================================================================
// QUERY HOOKS (Read Operations)
// ============================================================================

/**
 * Fetch list of patients with optional search
 *
 * @param params - Optional search parameters
 * @param token - Optional auth token for server-side queries
 *
 * @example
 * function PatientList() {
 *   const { data, isLoading, error } = usePatientList()
 *   if (isLoading) return <div>Loading...</div>
 *   return <div>{data.data.length} patients</div>
 * }
 */
export function usePatientList(params?: PatientListParams, token?: string) {
  return useQuery({
    queryKey: queryKeys.patients.list(params),
    queryFn: () => listPatients(params, token),
    staleTime: 60 * 1000, // 1 minute (matches global default)
  })
}

/**
 * Fetch a single patient by ID
 *
 * @param patientId - Patient ID
 * @param token - Optional auth token
 * @param options - Query options
 *
 * @example
 * function PatientDetails({ id }: { id: string }) {
 *   const { data: patient, isLoading } = usePatient(id)
 *   if (isLoading) return <div>Loading...</div>
 *   return <h1>{patient.first_name} {patient.last_name}</h1>
 * }
 */
export function usePatient(
  patientId: string,
  token?: string,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.patients.detail(patientId),
    queryFn: () => getPatient(patientId, token),
    staleTime: 60 * 1000,
    ...options,
  })
}

// ============================================================================
// MUTATION HOOKS (Write Operations)
// ============================================================================

/**
 * Create a new patient
 *
 * Features:
 * - Optimistic update to patient list
 * - Automatic cache invalidation on success
 * - Error handling with ApiError
 *
 * @param token - Optional auth token
 *
 * @example
 * function CreatePatientForm() {
 *   const createMutation = useCreatePatient()
 *
 *   const handleSubmit = async (data: CreatePatientRequest) => {
 *     try {
 *       const patient = await createMutation.mutateAsync(data)
 *       console.log("Created:", patient.id)
 *     } catch (error) {
 *       if (error instanceof ApiError) {
 *         console.error(error.code, error.message)
 *       }
 *     }
 *   }
 *
 *   return <form onSubmit={handleSubmit}>...</form>
 * }
 */
export function useCreatePatient(token?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: CreatePatientRequest) => createPatient(data, token),
    onSuccess: () => {
      // Invalidate all patient lists (all search variations)
      queryClient.invalidateQueries({ queryKey: queryKeys.patients.lists() })
    },
  })
}

/**
 * Update a patient's information
 *
 * Features:
 * - Optimistic update to specific patient
 * - Rollback on error
 * - Invalidates patient lists on success
 *
 * @param token - Optional auth token
 *
 * @example
 * function EditPatientForm({ patientId }: { patientId: string }) {
 *   const updateMutation = useUpdatePatient()
 *
 *   const handleSave = async (data: UpdatePatientRequest) => {
 *     await updateMutation.mutateAsync({ patientId, data })
 *   }
 *
 *   return <form>...</form>
 * }
 */
export function useUpdatePatient(token?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      patientId,
      data,
    }: {
      patientId: string
      data: UpdatePatientRequest
    }) => updatePatient(patientId, data, token),

    // Optimistic update
    onMutate: async ({ patientId, data }) => {
      // Cancel outgoing refetches
      await queryClient.cancelQueries({
        queryKey: queryKeys.patients.detail(patientId),
      })

      // Snapshot previous value
      const previousPatient = queryClient.getQueryData<PatientResponse>(
        queryKeys.patients.detail(patientId)
      )

      // Optimistically update
      if (previousPatient) {
        queryClient.setQueryData<PatientResponse>(
          queryKeys.patients.detail(patientId),
          { ...previousPatient, ...data }
        )
      }

      return { previousPatient }
    },

    // Rollback on error
    onError: (_error, { patientId }, context) => {
      if (context?.previousPatient) {
        queryClient.setQueryData(
          queryKeys.patients.detail(patientId),
          context.previousPatient
        )
      }
    },

    // Always refetch after error or success
    onSettled: (_data, _error, { patientId }) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.patients.detail(patientId),
      })
      queryClient.invalidateQueries({ queryKey: queryKeys.patients.lists() })
    },
  })
}

/**
 * Delete a patient and all their sessions
 *
 * IMPORTANT: This is destructive - deletes patient and all sessions.
 *
 * Features:
 * - Removes patient from cache immediately (optimistic)
 * - Invalidates related session queries
 * - Rollback on error
 *
 * @param token - Optional auth token
 *
 * @example
 * function DeletePatientButton({ patientId }: { patientId: string }) {
 *   const deleteMutation = useDeletePatient()
 *
 *   const handleDelete = async () => {
 *     if (!confirm("Delete patient and all sessions?")) return
 *     await deleteMutation.mutateAsync(patientId)
 *   }
 *
 *   return <button onClick={handleDelete}>Delete</button>
 * }
 */
export function useDeletePatient(token?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (patientId: string) => deletePatient(patientId, token),

    onSuccess: (_data, patientId) => {
      // Remove patient from cache
      queryClient.removeQueries({
        queryKey: queryKeys.patients.detail(patientId),
      })

      // Invalidate patient lists
      queryClient.invalidateQueries({ queryKey: queryKeys.patients.lists() })

      // Invalidate sessions (patient's sessions are now gone)
      queryClient.invalidateQueries({ queryKey: queryKeys.sessions.all })
    },
  })
}
