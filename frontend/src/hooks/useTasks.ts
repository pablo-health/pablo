// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  completeTask,
  createTask,
  deleteTask,
  deleteTaskDocument,
  listTaskDocuments,
  listTasks,
  listTaskTemplates,
  updateTask,
  uploadTaskDocument,
} from "@/lib/api/tasks"
import { queryKeys } from "@/lib/api/queryKeys"
import type { TaskFilters, TaskPayload } from "@/types/tasks"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Every list a task write can change: the task lists and the compliance view of them. */
const TASK_WRITE_KEYS = [queryKeys.tasks.all, queryKeys.compliance.items()]

export function useTaskTemplates(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.tasks.templates(),
    queryFn: () => listTaskTemplates(token),
    // Catalog is registry-driven and rarely changes — cache for 10 min.
    staleTime: 10 * 60 * 1000,
  })
}

export function useTasks(filters: TaskFilters = {}, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.tasks.list({ ...filters }),
    queryFn: () => listTasks(filters, token),
  })
}

export function useCreateTask(token?: string) {
  return useAuthMutation({
    mutationFn: (payload: TaskPayload) => createTask(payload, token),
    invalidateKeys: TASK_WRITE_KEYS,
  })
}

export function useUpdateTask(token?: string) {
  return useAuthMutation({
    mutationFn: ({ id, payload }: { id: string; payload: TaskPayload }) =>
      updateTask(id, payload, token),
    invalidateKeys: TASK_WRITE_KEYS,
  })
}

export function useCompleteTask(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => completeTask(id, token),
    invalidateKeys: TASK_WRITE_KEYS,
  })
}

export function useDeleteTask(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => deleteTask(id, token),
    invalidateKeys: TASK_WRITE_KEYS,
  })
}

// --- Evidence documents ---------------------------------------------------

export function useTaskDocuments(taskId: string, enabled = true, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.tasks.documents(taskId),
    queryFn: () => listTaskDocuments(taskId, token),
    enabled: enabled && Boolean(taskId),
  })
}

export function useUploadTaskDocument(taskId: string, token?: string) {
  return useAuthMutation({
    mutationFn: ({
      file,
      documentType,
      description,
    }: {
      file: File
      documentType: string
      description?: string
    }) => uploadTaskDocument(taskId, file, documentType, description, token),
    invalidateKeys: [queryKeys.tasks.documents(taskId), queryKeys.compliance.documents(taskId)],
  })
}

export function useDeleteTaskDocument(taskId: string, token?: string) {
  return useAuthMutation({
    mutationFn: (documentId: string) => deleteTaskDocument(documentId, token),
    invalidateKeys: [queryKeys.tasks.documents(taskId), queryKeys.compliance.documents(taskId)],
  })
}
