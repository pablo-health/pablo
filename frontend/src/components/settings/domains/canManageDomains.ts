// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { UserStatus } from "@/lib/api/users"

/**
 * Who may change the practice's domains and its website. The owner today;
 * when a practice administrator role exists, this and the server's check
 * (`_manageable_practice_id` in backend/app/routes/practice_domains.py) are
 * what widen.
 */
export function canManageDomains(status: UserStatus | undefined): boolean {
  return status?.is_practice_owner === true
}
