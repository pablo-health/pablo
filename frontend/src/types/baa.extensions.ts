/**
 * BAA type slot.
 *
 * BAA acceptance is provided by an optional backend overlay. These stub
 * shapes exist so that the shared API module (`users.ts`, which imports from
 * `@/types/baa`) compiles when the overlay is not present. A downstream build
 * overwrites *this file only* to supply the real contract and any client-side
 * form types; `baa.ts` re-exports whatever lives here, so it is never forked.
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
