// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What each part of the client portal is called where a practice chooses it.
 * One list, read by the portal settings card and the first-client prompt, so
 * the two never name the same part differently.
 */
const PORTAL_MODULE_LABELS: Record<string, string> = {
  intake: "Forms",
  messaging: "Messages",
  documents: "Documents",
  appointments: "Appointments",
  refills: "Refill requests",
  billing: "Billing",
}

export function portalModuleLabel(name: string): string {
  return PORTAL_MODULE_LABELS[name] ?? name
}
