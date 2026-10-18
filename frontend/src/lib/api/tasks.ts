// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tasks API
 *
 * Type-safe wrappers for /api/tasks — everything the clinician has to do,
 * compliance reminders included (`category: "compliance"`).
 */

import type { ComplianceDocument, ComplianceTemplate } from "@/types/compliance"
import type { Task, TaskFilters, TaskPayload } from "@/types/tasks"
import { del, get, getBlob, post, postForm, put } from "./client"

export async function listTaskTemplates(token?: string): Promise<ComplianceTemplate[]> {
  return get<ComplianceTemplate[]>("/api/tasks/templates", token)
}

export async function listTasks(filters: TaskFilters = {}, token?: string): Promise<Task[]> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value)
  }
  const query = params.toString()
  return get<Task[]>(query ? `/api/tasks?${query}` : "/api/tasks", token)
}

export async function getTask(id: string, token?: string): Promise<Task> {
  return get<Task>(`/api/tasks/${id}`, token)
}

export async function createTask(payload: TaskPayload, token?: string): Promise<Task> {
  return post<Task>("/api/tasks", payload, token)
}

export async function updateTask(id: string, payload: TaskPayload, token?: string): Promise<Task> {
  return put<Task>(`/api/tasks/${id}`, payload, token)
}

export async function completeTask(id: string, token?: string): Promise<Task> {
  return post<Task>(`/api/tasks/${id}/complete`, {}, token)
}

export async function deleteTask(id: string, token?: string): Promise<void> {
  return del<void>(`/api/tasks/${id}`, token)
}

// --- Evidence documents ---------------------------------------------------

export async function listTaskDocuments(
  taskId: string,
  token?: string,
): Promise<ComplianceDocument[]> {
  return get<ComplianceDocument[]>(`/api/tasks/${taskId}/documents`, token)
}

export async function uploadTaskDocument(
  taskId: string,
  file: File,
  documentType: string,
  description?: string,
  token?: string,
): Promise<ComplianceDocument> {
  const formData = new FormData()
  formData.append("file", file)
  formData.append("document_type", documentType)
  if (description) formData.append("description", description)
  return postForm<ComplianceDocument>(`/api/tasks/${taskId}/documents`, formData, token)
}

export async function downloadTaskDocument(documentId: string, token?: string): Promise<Blob> {
  return getBlob(`/api/tasks/documents/${documentId}/file`, token)
}

export async function deleteTaskDocument(documentId: string, token?: string): Promise<void> {
  return del<void>(`/api/tasks/documents/${documentId}`, token)
}
