// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Button } from "@/components/ui/button"
import {
  useCheckPracticeDomains,
  useMakePracticeDomainPrimary,
  usePracticeDomains,
  useRemovePracticeDomain,
} from "@/hooks/usePracticeDomains"
import { useDomainConnect } from "@/hooks/useDomainConnect"
import { ApiError } from "@/lib/api/client"
import type { DomainPurpose, PracticeDomain } from "@/lib/api/practiceDomains"
import type { UserStatus } from "@/lib/api/users"
import { AddDomainForm } from "../domains/AddDomainForm"
import { DomainConnectOffers } from "../domains/DomainConnectOffers"
import { DomainConnectReturn } from "../domains/DomainConnectReturn"
import { DomainRow } from "../domains/DomainRow"
import { SettingsCard } from "../ui"
import { useSettingsUserStatus } from "../useSettingsPreferences"

const SECTIONS: { purpose: DomainPurpose; title: string; description: string }[] = [
  { purpose: "portal", title: "Client portal", description: "Addresses your clients can use to reach your portal." },
  { purpose: "site", title: "Website", description: "Addresses for your practice's website." },
]

/**
 * Who may change the practice's domains. The owner today; when a practice
 * administrator role exists, this and the server's check are what widen.
 */
function canManageDomains(status: UserStatus | undefined): boolean {
  return status?.is_practice_owner === true
}

/** Practice > Domains. The practice's own addresses for its portal and website. */
export function DomainsPage() {
  const { data: userStatus } = useSettingsUserStatus()
  const { data, isLoading, isError } = usePracticeDomains()
  const makePrimary = useMakePracticeDomainPrimary()
  const remove = useRemovePracticeDomain()
  const check = useCheckPracticeDomains()

  const canManage = canManageDomains(userStatus)
  const connect = useDomainConnect(canManage)
  const busy = makePrimary.isPending || remove.isPending || check.isPending
  const failure = makePrimary.error ?? remove.error ?? check.error
  const failureMessage =
    failure instanceof ApiError && failure.message
      ? failure.message
      : failure === check.error && failure
        ? "Your records couldn't be checked. Try again."
        : failure
          ? "That change couldn't be saved. Try again."
          : null

  if (isLoading) return null
  if (isError || !data) {
    return (
      <SettingsCard>
        <p role="alert" className="text-sm text-muted-foreground">
          Your domains couldn&apos;t be loaded. Try again.
        </p>
      </SettingsCard>
    )
  }

  const byPurpose = (purpose: DomainPurpose): PracticeDomain[] =>
    data.domains.filter((d) => d.purpose === purpose)

  return (
    <>
      <DomainConnectReturn />
      {userStatus && !canManage && (
        <p className="mb-3 text-[12.5px] text-muted-foreground">Only the practice owner can change domains.</p>
      )}
      {failureMessage && (
        <p role="alert" className="mb-3 text-[12.5px] text-red-700">
          {failureMessage}
        </p>
      )}
      {canManage && data.domains.length > 0 && (
        <div className="mb-3 flex items-center gap-3">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => check.mutate()}>
            {check.isPending ? "Checking…" : "Check now"}
          </Button>
          <span className="text-[12.5px] text-muted-foreground">Looks up your DNS records.</span>
        </div>
      )}
      {SECTIONS.map(({ purpose, title, description }) => {
        const domains = byPurpose(purpose)
        return (
          <SettingsCard key={purpose} title={title} description={description}>
            {canManage && <DomainConnectOffers purpose={purpose} domains={connect.data?.domains} />}
            {domains.length === 0 ? (
              <p className="text-sm text-muted-foreground">No domains yet.</p>
            ) : (
              <ul aria-label={`${title} domains`}>
                {domains.map((domain) => (
                  <DomainRow
                    key={domain.domain}
                    domain={domain}
                    canManage={canManage}
                    busy={busy}
                    onMakePrimary={(host) => makePrimary.mutate(host)}
                    onRemove={(host) => remove.mutate(host)}
                  />
                ))}
              </ul>
            )}
          </SettingsCard>
        )
      })}
      {canManage && (
        <SettingsCard title="Add a domain" description="Use a domain your practice owns.">
          <AddDomainForm />
        </SettingsCard>
      )}
    </>
  )
}
