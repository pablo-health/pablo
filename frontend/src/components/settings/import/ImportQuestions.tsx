// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * The three questions an import can ask before it lands anything:
 *
 * - Two clients share a name and a record says only the name — whose is it?
 * - A client in the export has the same name as a client already here, with
 *   nothing to confirm it — same person, or a different one?
 * - The export names the clinician who wrote each note — is that you?
 *
 * Each answer is kept in the parent's decisions and sent with Apply. Records
 * the import could attribute on its own are never asked about.
 */

import type { ImportClient, ImportDecisions, ImportPreview, ImportRecord } from "@/lib/api/migration"
import { recordKey } from "@/lib/api/migration"
import { ImportFileViewer } from "./ImportFileViewer"

interface Props {
  runId: string
  preview: ImportPreview
  decisions: ImportDecisions
  onChange: (next: ImportDecisions) => void
}

function describe(client: ImportClient | undefined): string {
  if (!client) return "Unknown client"
  const parts = [client.display_name]
  if (client.birthday) parts.push(`born ${client.birthday}`)
  if (client.email) parts.push(client.email)
  if (client.address) parts.push(client.address.city)
  return parts.join(" · ")
}

export function ImportQuestions({ runId, preview, decisions, onChange }: Props) {
  const clients = new Map(preview.clients.map((c) => [c.card_id, c]))
  const records = new Map(preview.records.map((r) => [recordKey(r.record_type, r.source_id), r]))

  const assign = (key: string, value: string) =>
    onChange({ ...decisions, assignments: { ...decisions.assignments, [key]: value } })
  const decideDuplicate = (cardId: string, value: string) =>
    onChange({ ...decisions, duplicates: { ...decisions.duplicates, [cardId]: value } })
  const mapProvider = (name: string, isMe: boolean) => {
    const providers = { ...decisions.providers }
    if (isMe) providers[name] = "me"
    else delete providers[name]
    onChange({ ...decisions, providers })
  }

  const { same_name: sameName, duplicates, providers } = preview.questions
  if (!sameName.length && !duplicates.length && !providers.length) return null

  return (
    <div className="space-y-6">
      {sameName.map((group) => (
        <section key={group.folder_name} aria-label={`Two clients are named ${group.folder_name}`}>
          <h3 className="text-sm font-semibold text-foreground">
            Two clients are named {group.folder_name}. Which one is each of these for?
          </h3>
          <ul className="mt-2 space-y-3">
            {group.records.map((ref) => {
              const key = recordKey(ref.record_type, ref.source_id)
              const record = records.get(key) as ImportRecord | undefined
              const label = record ? `${record.label}${record.when ? `, ${record.when}` : ""}` : key
              return (
                <li key={key} className="rounded-lg border border-border p-3" data-testid="same-name-record">
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-sm text-foreground">{label}</span>
                    {record && <ImportFileViewer runId={runId} path={record.path} label={label} />}
                  </div>
                  <fieldset className="mt-2 space-y-1">
                    <legend className="sr-only">{`Who ${label} is for`}</legend>
                    {group.candidates.map((cardId) => (
                      <label key={cardId} className="flex items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name={key}
                          checked={decisions.assignments[key] === cardId}
                          onChange={() => assign(key, cardId)}
                        />
                        {describe(clients.get(cardId))}
                      </label>
                    ))}
                    <label className="flex items-center gap-2 text-sm text-muted-foreground">
                      <input
                        type="radio"
                        name={key}
                        checked={decisions.assignments[key] === "skip"}
                        onChange={() => assign(key, "skip")}
                      />
                      Don&apos;t import this
                    </label>
                  </fieldset>
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      {duplicates.map((dup) => {
        const client = clients.get(dup.card_id)
        return (
          <section key={dup.card_id} aria-label={`Is ${client?.display_name} already here?`}>
            <h3 className="text-sm font-semibold text-foreground">
              You already have a client named {client?.folder_name}. Is this the same person?
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">{describe(client)}</p>
            <fieldset className="mt-2 space-y-1">
              <legend className="sr-only">{`Same person as ${client?.folder_name}`}</legend>
              {dup.possible_duplicates.map((patientId) => (
                <label key={patientId} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name={`dup-${dup.card_id}`}
                    checked={decisions.duplicates[dup.card_id] === `merge:${patientId}`}
                    onChange={() => decideDuplicate(dup.card_id, `merge:${patientId}`)}
                  />
                  Same person — add to the existing client
                </label>
              ))}
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name={`dup-${dup.card_id}`}
                  checked={decisions.duplicates[dup.card_id] === "create"}
                  onChange={() => decideDuplicate(dup.card_id, "create")}
                />
                Different person — add a new client
              </label>
            </fieldset>
          </section>
        )
      })}

      {providers.length > 0 && (
        <section aria-label="Who wrote these notes">
          <h3 className="text-sm font-semibold text-foreground">Who wrote these notes?</h3>
          <ul className="mt-2 space-y-1">
            {providers.map((p) => (
              <li key={p.name}>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={decisions.providers[p.name] === "me"}
                    onChange={(e) => mapProvider(p.name, e.target.checked)}
                  />
                  {`${p.name} is me`}
                </label>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
