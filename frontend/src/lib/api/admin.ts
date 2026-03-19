// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Admin API Functions
 *
 * Type-safe wrappers for admin-only API endpoints.
 */

import type {
  ExportActionRequest,
  ExportQueueListResponse,
} from "@/types/sessions"
import { del, get, patch, post } from "./client"

// --- Tenant types ---

interface TenantListItem {
  tenant_id: string
  practice_name: string
  owner_email: string
  database_name: string
  status: string
  created_at: string | null
}

interface TenantListResponse {
  data: TenantListItem[]
  total: number
}

interface TenantDeleteResponse {
  message: string
  tenant_id: string
  cleanup_errors: string[]
}

// --- User types ---

interface UserListItem {
  id: string
  email: string
  name: string
  status: string
  is_admin: boolean
  mfa_enrolled_at: string | null
  baa_accepted_at: string | null
  created_at: string
}

interface UserListResponse {
  data: UserListItem[]
  total: number
}

interface AllowlistEntry {
  email: string
  added_by: string
  added_at: string
}

interface AllowlistResponse {
  data: AllowlistEntry[]
  total: number
}

// --- Tenant API functions ---

export async function listTenants(
  token?: string
): Promise<TenantListResponse> {
  return get<TenantListResponse>("/api/admin/tenants", token)
}

export async function disableTenant(
  tenantId: string,
  token?: string
): Promise<{ message: string; tenant_id: string }> {
  return patch<{ message: string; tenant_id: string }>(
    `/api/admin/tenants/${tenantId}/disable`,
    {},
    token
  )
}

export async function enableTenant(
  tenantId: string,
  token?: string
): Promise<{ message: string; tenant_id: string }> {
  return patch<{ message: string; tenant_id: string }>(
    `/api/admin/tenants/${tenantId}/enable`,
    {},
    token
  )
}

export async function deleteTenant(
  tenantId: string,
  token?: string
): Promise<TenantDeleteResponse> {
  return del<TenantDeleteResponse>(`/api/admin/tenants/${tenantId}`, token)
}

export async function createPractice(
  email: string,
  practiceName: string,
  token?: string
): Promise<{ status: string; tenant_id: string | null }> {
  await addToAllowlist(email, token)
  return post<{ status: string; tenant_id: string | null }>(
    "/api/auth/signup",
    { email, practice_name: practiceName },
    token
  )
}

// --- Export API functions ---

/**
 * List sessions queued for export review
 *
 * Returns all sessions with export_status=pending_review across all users.
 * Requires admin privileges (bypassed in dev mode).
 *
 * @param token - Optional auth token for server-side calls
 * @returns List of queued sessions with redacted content
 *
 * @example
 * const queue = await listExportQueue()
 * console.log(queue.total) // Number of sessions awaiting review
 * queue.data.forEach(session => {
 *   console.log(session.patient_name, session.redacted_transcript)
 * })
 */
export async function listExportQueue(
  token?: string
): Promise<ExportQueueListResponse> {
  return get<ExportQueueListResponse>("/api/admin/export-queue", token)
}

/**
 * Perform action on queued export session
 *
 * Actions:
 * - approve: Set status to "approved" (ready for export)
 * - skip: Set status to "skipped" (remove from queue)
 * - flag: Set status to "skipped" with reason (PII concern)
 *
 * @param sessionId - Session ID to act on
 * @param data - Action to perform (approve/skip/flag) with optional reason
 * @param token - Optional auth token for server-side calls
 * @returns Success message with new status
 *
 * @example
 * // Approve session for export
 * await performExportAction("session-123", { action: "approve" })
 *
 * // Skip session
 * await performExportAction("session-123", { action: "skip" })
 *
 * // Flag session with reason
 * await performExportAction("session-123", {
 *   action: "flag",
 *   reason: "Patient name not fully redacted in transcript"
 * })
 */
export async function performExportAction(
  sessionId: string,
  data: ExportActionRequest,
  token?: string
): Promise<{ message: string; session_id: string; export_status: string }> {
  return post<{ message: string; session_id: string; export_status: string }>(
    `/api/admin/export-queue/${sessionId}/action`,
    data,
    token
  )
}

/**
 * List all users in the system
 *
 * @param token - Optional auth token for server-side calls
 * @returns List of users with status and metadata
 */
export async function listUsers(token?: string): Promise<UserListResponse> {
  return get<UserListResponse>("/api/admin/users", token)
}

/**
 * Disable a user account
 *
 * @param userId - User ID to disable
 * @param token - Optional auth token for server-side calls
 * @returns Success message with user ID
 */
export async function disableUser(
  userId: string,
  token?: string
): Promise<{ message: string; user_id: string }> {
  return patch<{ message: string; user_id: string }>(
    `/api/admin/users/${userId}/disable`,
    {},
    token
  )
}

/**
 * Enable a user account
 *
 * @param userId - User ID to enable
 * @param token - Optional auth token for server-side calls
 * @returns Success message with user ID
 */
export async function enableUser(
  userId: string,
  token?: string
): Promise<{ message: string; user_id: string }> {
  return patch<{ message: string; user_id: string }>(
    `/api/admin/users/${userId}/enable`,
    {},
    token
  )
}

/**
 * List all emails in the allowlist
 *
 * @param token - Optional auth token for server-side calls
 * @returns List of allowlisted emails
 */
export async function listAllowlist(
  token?: string
): Promise<AllowlistResponse> {
  return get<AllowlistResponse>("/api/admin/allowlist", token)
}

/**
 * Add an email to the allowlist
 *
 * @param email - Email address to allowlist
 * @param token - Optional auth token for server-side calls
 * @returns Success message with email
 */
export async function addToAllowlist(
  email: string,
  token?: string
): Promise<{ message: string; email: string }> {
  return post<{ message: string; email: string }>(
    "/api/admin/allowlist",
    { email },
    token
  )
}

/**
 * Remove an email from the allowlist
 *
 * @param email - Email address to remove
 * @param token - Optional auth token for server-side calls
 * @returns Success message with email
 */
export async function removeFromAllowlist(
  email: string,
  token?: string
): Promise<{ message: string; email: string }> {
  return del<{ message: string; email: string }>(
    `/api/admin/allowlist/${email}`,
    token
  )
}
