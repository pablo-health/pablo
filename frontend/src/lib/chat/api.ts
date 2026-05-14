// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Lifecycle wrappers for the ``/api/chat/conversations`` endpoints.
 * Streaming (POST ``/messages``) lives in ``./sse.ts`` because the
 * shared ``apiClient`` decodes JSON bodies up-front, which would break
 * SSE.
 */

import { apiClient } from "@/lib/api/client"

import type {
  ChatConversation,
  ChatConversationDetail,
  CreateChatConversationRequest,
  UpdateChatConversationRequest,
} from "./types"

interface ListResponse {
  data: ChatConversation[]
  total: number
}

export async function createConversation(
  body: CreateChatConversationRequest,
): Promise<ChatConversation> {
  return apiClient<ChatConversation>("/api/chat/conversations", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export async function getConversation(
  conversationId: string,
): Promise<ChatConversationDetail> {
  return apiClient<ChatConversationDetail>(
    `/api/chat/conversations/${encodeURIComponent(conversationId)}`,
    { method: "GET" },
  )
}

export interface ListConversationsParams {
  patientId: string
  callerFeatureKey?: string
  includeArchived?: boolean
  page?: number
  pageSize?: number
}

export async function listConversations(
  params: ListConversationsParams,
): Promise<ListResponse> {
  const qs = new URLSearchParams({ patient_id: params.patientId })
  if (params.callerFeatureKey) qs.set("caller_feature_key", params.callerFeatureKey)
  if (params.includeArchived) qs.set("include_archived", "true")
  if (params.page) qs.set("page", String(params.page))
  if (params.pageSize) qs.set("page_size", String(params.pageSize))
  return apiClient<ListResponse>(`/api/chat/conversations?${qs.toString()}`, {
    method: "GET",
  })
}

export async function updateConversation(
  conversationId: string,
  body: UpdateChatConversationRequest,
): Promise<ChatConversation> {
  return apiClient<ChatConversation>(
    `/api/chat/conversations/${encodeURIComponent(conversationId)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  )
}

export async function deleteConversation(
  conversationId: string,
  mode: "purge" | "archive" = "purge",
): Promise<void> {
  await apiClient<unknown>(
    `/api/chat/conversations/${encodeURIComponent(conversationId)}?mode=${mode}`,
    { method: "DELETE" },
  )
}
