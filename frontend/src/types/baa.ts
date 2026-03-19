/**
 * BAA type stubs for Pablo Solo.
 *
 * BAA acceptance is a SaaS-only feature. These types exist so that
 * shared API modules (users.ts) compile without modification.
 */

export interface BAAStatusResponse {
  accepted: boolean
  accepted_at: string | null
  version: string | null
  current_version: string | null
  needs_update: boolean
}

export interface AcceptBAARequest {
  legal_name: string
  license_number: string
  license_state: string
  practice_name: string
  business_address: string
  version: string
  accepted: boolean
}
