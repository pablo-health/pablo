/**
 * BAA type stubs.
 *
 * BAA acceptance is provided by an optional backend overlay. These
 * stubs exist so that shared API modules (users.ts) compile when the
 * overlay is not present.
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
