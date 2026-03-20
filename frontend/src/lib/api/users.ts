// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * User API Functions
 *
 * API functions for user-related endpoints, including BAA (Business Associate Agreement).
 */

import type { AcceptBAARequest, BAAStatusResponse } from "@/types/baa"
import { get, post, put } from "./client"

interface UserProfile {
  id: string
  email: string
  name: string
  status: string
  mfa_enrolled_at: string | null
  is_admin: boolean
  baa_accepted_at: string | null
}

interface UserStatus {
  status: string
  mfa_enrolled_at: string | null
  is_admin: boolean
  name: string
  email: string
}

/**
 * Get the current user's status without requiring MFA.
 *
 * Used by the dashboard layout to check if the user needs MFA enrollment
 * or is disabled, before MFA is fully set up.
 *
 * @param token - Optional auth token for server-side calls
 * @returns User status with MFA enrollment info
 */
export async function getUserStatus(
  token?: string
): Promise<UserStatus> {
  return get<UserStatus>("/api/users/me/status", token)
}

/**
 * Get the current user's profile (requires MFA)
 *
 * @param token - Optional auth token for server-side calls
 * @returns User profile with status and MFA enrollment info
 */
export async function getUserProfile(
  token?: string
): Promise<UserProfile> {
  return get<UserProfile>("/api/users/me", token)
}

/**
 * Get the current user's BAA acceptance status
 *
 * @param token - Optional auth token for server-side calls
 * @returns BAA status including acceptance state and version info
 *
 * @example
 * const status = await getBAAStatus()
 * if (!status.accepted) {
 *   // Redirect to BAA acceptance page
 * }
 */
export async function getBAAStatus(
  token?: string
): Promise<BAAStatusResponse> {
  return get<BAAStatusResponse>("/api/users/me/baa-status", token)
}

/**
 * Get the BAA text (markdown format)
 *
 * @param version - Optional specific version (e.g., "2024-01-01"). If not provided, returns current version.
 * @param token - Optional auth token for server-side calls
 * @returns BAA text in markdown format
 *
 * @example
 * const baaText = await getBAAText()
 * // Returns markdown text of current BAA version
 *
 * const oldVersion = await getBAAText("2024-01-01")
 * // Returns markdown text of specific version
 */
export async function getBAAText(
  version?: string,
  token?: string
): Promise<string> {
  const endpoint = version ? `/api/users/baa/${version}` : "/api/users/baa"
  return get<string>(endpoint, token)
}

/**
 * Accept the Business Associate Agreement
 *
 * Submits the user's acceptance with their professional credentials.
 * Upon successful acceptance, the backend records:
 * - Acceptance timestamp
 * - BAA version accepted
 * - Professional credentials (legal name, license info, practice info)
 * - Full BAA text for audit trail
 *
 * @param data - Acceptance request with professional information
 * @param token - Optional auth token for server-side calls
 * @returns Updated BAA status
 * @throws ApiError if acceptance fails (e.g., validation error, version not found)
 *
 * @example
 * const result = await acceptBAA({
 *   legal_name: "Dr. Jane Smith",
 *   license_number: "PSY12345",
 *   license_state: "CA",
 *   practice_name: "Smith Therapy Services",
 *   business_address: "123 Main St, San Francisco, CA 94101",
 *   version: "2024-01-01",
 *   accepted: true
 * })
 *
 * if (result.accepted) {
 *   // Redirect to dashboard
 * }
 */
export async function acceptBAA(
  data: AcceptBAARequest,
  token?: string
): Promise<BAAStatusResponse> {
  return post<BAAStatusResponse>("/api/users/me/accept-baa", data, token)
}

export interface UserPreferences {
  default_video_platform: string
  default_session_type: string
  default_duration_minutes: number
  auto_transcribe: boolean
  quality_preset: string
  therapist_display_name: string | null
  working_hours_start: number
  working_hours_end: number
}

export async function getPreferences(
  token?: string
): Promise<UserPreferences> {
  return get<UserPreferences>("/api/users/me/preferences", token)
}

export async function savePreferences(
  prefs: UserPreferences,
  token?: string
): Promise<UserPreferences> {
  return put<UserPreferences>("/api/users/me/preferences", prefs, token)
}
