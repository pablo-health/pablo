// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useState } from "react"

import {
  deletePatientDocument,
  finalizePatientDocumentUpload,
  initPatientDocumentUpload,
  listPatientDocuments,
  uploadFileToSignedUrl,
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

/**
 * Two-step signed-URL upload as a single mutation.
 *
 * Tracks the in-flight stage so a long-running PUT can show progress
 * UX. Init failures surface the backend error envelope (size, mime);
 * the GCS PUT step throws a plain Error.
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
      await uploadFileToSignedUrl(
        init.upload_url,
        file,
        init.max_bytes,
        init.required_content_type,
      )
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
