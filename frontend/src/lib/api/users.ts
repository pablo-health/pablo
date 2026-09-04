// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * User API Functions
 *
 * API functions for user-related endpoints, including BAA (Business Associate Agreement).
 */

import type { AcceptBAARequest, BAAStatusResponse } from "@/types/baa"
import type { ThemeId } from "@/lib/theme"
import { get, patch, post, put } from "./client"
import type {
  UpdateUserRequestExtensions,
  UserStatusExtensions,
} from "./users-extensions"

// Surface any extra user-API types / functions a downstream build registers in
// the extension slot, so consumers keep importing from `@/lib/api/users`.
export * from "./users-extensions"

export type ProviderType = "therapist" | "prescriber" | "both"

export interface UserProfile {
  id: string
  email: string
  name: string
  status: string
  mfa_enrolled_at: string | null
  is_platform_admin: boolean
  baa_accepted_at: string | null
  provider_type: ProviderType | null
  phone: string | null
}

export interface UserStatusBase {
  status: string
  mfa_enrolled_at: string | null
  is_platform_admin: boolean
  name: string
  email: string
  /** Version of the security & privacy guide the user last acknowledged. */
  security_guide_version: string | null
  security_guide_acknowledged_at: string | null
  /**
   * Practice id for the current user, when multi-tenancy is enabled.
   * Undefined in single-tenant deployments.
   */
  practice_id?: string
  /**
   * "therapist" | "prescriber" | "both". `null` means the user has not
   * picked a provider type yet; downstream onboarding flows treat this
   * as the "needs onboarding" signal.
   */
  provider_type: ProviderType | null
  /**
   * Set when the user explicitly completes the profile-basics onboarding step.
   * Null for users who have never gone through the step (e.g. Google auth users
   * whose name was pre-filled). The wizard gates on this rather than name
   * presence so every user sees the step and can set title/credentials/phone.
   */
  profile_basics_completed_at: string | null
  /**
   * "in_progress" | "completed" | null. Set by the client via
   * `updateUserProfile` as onboarding steps finish; onboarding surfaces
   * can key an optional step's gate off this rather than adding a
   * dedicated backend field per step.
   */
  onboarding_state: string | null
}

/**
 * The user-status shape. A downstream build may widen it with extra fields via
 * `UserStatusExtensions` in the extension slot; here that is `unknown`, so this
 * is exactly `UserStatusBase`.
 */
export type UserStatus = UserStatusBase & UserStatusExtensions

export interface UpdateUserRequestBase {
  name?: string
  title?: string
  credentials?: string
  provider_type?: ProviderType
  /** Optional contact number. May be used for account recovery or support. */
  phone?: string
  /** Set to true when the user explicitly submits the profile-basics step. */
  profile_basics_completed?: boolean
  /**
   * Mirrors the backend's ``OnboardingState`` literal. "later" is a real,
   * persisted state — the deferral path writes it and the dashboard reads it —
   * so omitting it here made the type narrower than the endpoint it describes.
   */
  onboarding_state?: "in_progress" | "later" | "completed"
}

export type UpdateUserRequest = UpdateUserRequestBase & UpdateUserRequestExtensions

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
 * Partial update of the current user's profile. Currently the backend
 * persists `name` and `provider_type`; `title` / `credentials` are
 * accepted by the schema but live on the per-practice clinician
 * profile and are not wired through yet.
 */
export async function updateUserProfile(
  data: UpdateUserRequest,
  token?: string
): Promise<UserProfile> {
  return patch<UserProfile>("/api/users/me", data, token)
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
  calendar_default_view: string
  timezone: string
  theme: ThemeId
  calendar_density: "gentle" | "balanced" | "compact"
  /** Set once the first-visit calendar setup wizard has been finished or
   * waved away. Optional because a response from before the flag existed
   * simply lacks it, which reads as "not yet". */
  calendar_setup_complete?: boolean
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

export async function saveThemePreference(
  theme: ThemeId,
  token?: string
): Promise<UserPreferences> {
  return put<UserPreferences>("/api/users/me/preferences/theme", { theme }, token)
}
