// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useState } from "react"

import {
  deletePatientDocument,
  finalizePatientDocumentUpload,
  getPatientDocument,
  initPatientDocumentUpload,
  listPatientDocuments,
  uploadFileToStorage,
} from "@/lib/api/patientDocuments"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  DocumentCategory,
  PatientDocumentListResponse,
  PatientDocumentResponse,
} from "@/types/patientDocuments"

import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function usePatientDocuments(
  patientId: string | undefined,
  token?: string,
) {
  return useAuthQuery<PatientDocumentListResponse>({
    queryKey: queryKeys.patientDocuments.byPatient(patientId ?? ""),
    queryFn: () => listPatientDocuments(patientId!, token),
    enabled: !!patientId,
  })
}

// ~2 minutes at the 3s poll interval below — comfortably above the known
// worst case (Doc AI's ~60s attempt + one retry) without polling forever
// if a job is stuck. Exported so callers can recognize the same "gave up
// waiting" condition (react-query keeps the last-seen "pending" data once
// polling stops, so a status check alone can't tell the two apart).
export const EXTRACTION_POLL_TIMEOUT_TICKS = 40

/**
 * Polls a single document while its off-request text extraction is
 * running. Pass `null` to disable (no in-flight extraction to watch).
 * Stops polling once `extraction_status` leaves `"pending"`, or after
 * `EXTRACTION_POLL_TIMEOUT_TICKS` polls if it never does.
 */
export function usePatientDocument(
  documentId: string | null,
  token?: string,
) {
  // UseQueryResult doesn't expose the internal Query's dataUpdateCount, so
  // we track successful fetches ourselves (keyed off dataUpdatedAt, which
  // changes on every resolved fetch) to detect "gave up polling" in
  // callers (see EXTRACTION_POLL_TIMEOUT_TICKS doc comment above).
  const [pollCount, setPollCount] = useState(0)
  const query = useAuthQuery<PatientDocumentResponse>({
    queryKey: queryKeys.patientDocuments.detail(documentId ?? ""),
    queryFn: () => getPatientDocument(documentId!, token),
    enabled: documentId !== null,
    refetchInterval: (query) => {
      if (query.state.data?.extraction_status !== "pending") return false
      if (query.state.dataUpdateCount > EXTRACTION_POLL_TIMEOUT_TICKS) return false
      return 3000
    },
  })
  const { dataUpdatedAt } = query

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPollCount(0)
  }, [documentId])

  useEffect(() => {
    if (dataUpdatedAt === 0) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPollCount((count) => count + 1)
  }, [dataUpdatedAt])

  return { ...query, pollCount }
}

/**
 * Two-step signed-URL upload as a single mutation.
 *
 * Tracks the in-flight stage so a long-running upload can show
 * progress UX. Init failures surface the backend error envelope
 * (size, mime); the storage upload step throws a plain Error.
 */
export type UploadStage = "idle" | "init" | "uploading" | "finalize" | "done"

export function useUploadPatientDocument(
  patientId: string | undefined,
  token?: string,
) {
  const [stage, setStage] = useState<UploadStage>("idle")
  const mutation = useAuthMutation<
    PatientDocumentResponse,
    { file: File; category?: DocumentCategory }
  >({
    mutationFn: async ({ file, category }) => {
      if (!patientId) {
        throw new Error("patientId is required")
      }
      setStage("init")
      const init = await initPatientDocumentUpload(
        patientId,
        {
          filename: file.name,
          mime_type: file.type,
          size_bytes: file.size,
          category: category ?? "chart",
        },
        token,
      )
      setStage("uploading")
      await uploadFileToStorage(init.upload, file)
      setStage("finalize")
      const finalized = await finalizePatientDocumentUpload(
        init.document_id,
        token,
      )
      setStage("done")
      return finalized
    },
    invalidateKeys: () =>
      patientId
        ? [queryKeys.patientDocuments.byPatient(patientId)]
        : [],
  })

  const reset = useCallback(() => {
    setStage("idle")
    mutation.reset()
  }, [mutation])

  return { ...mutation, stage, reset }
}

export function useDeletePatientDocument(
  patientId: string | undefined,
  token?: string,
) {
  return useAuthMutation<{ message: string }, { documentId: string }>({
    mutationFn: ({ documentId }) => deletePatientDocument(documentId, token),
    invalidateKeys: () =>
      patientId
        ? [queryKeys.patientDocuments.byPatient(patientId)]
        : [],
  })
}
