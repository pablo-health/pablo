// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { SiteHeader, SiteHeaderLink } from "@/lib/api/practiceSite"

/** The portal shows at most five links (backend/app/sites/header.py). */
const MAX_LINKS = 5

/**
 * A portal header suggested from the draft's index.html, for a draft whose
 * theme.json declares none (backend/app/sites/suggest.py). The practice
 * accepts it as it is or edits it first; either way it is written into the
 * draft's theme.json and checked like any header there, and the portal shows
 * it once the draft is published. Until then it is only a suggestion.
 */
export function SuggestedHeader({
  header,
  canManage,
  busy,
  onSave,
}: {
  header: SiteHeader
  canManage: boolean
  busy: boolean
  onSave: (header: SiteHeader) => void
}) {
  const [editing, setEditing] = useState(false)

  return (
    <div data-testid="website-suggested-header" className="mt-3 rounded-md border border-border p-3 text-[12.5px]">
      <p className="font-medium">Portal header from your index.html</p>
      {editing ? (
        <HeaderForm
          initial={header}
          busy={busy}
          onCancel={() => setEditing(false)}
          onSave={(edited) => {
            onSave(edited)
            setEditing(false)
          }}
        />
      ) : (
        <>
          <HeaderPreview header={header} />
          {canManage && (
            <div className="mt-2 flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => onSave(header)}>
                Accept
              </Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(true)}>
                Edit
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function HeaderPreview({ header }: { header: SiteHeader }) {
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
      {header.wordmark && (
        <>
          <dt className="text-muted-foreground">Name</dt>
          <dd>{header.wordmark}</dd>
        </>
      )}
      {header.subtitle && (
        <>
          <dt className="text-muted-foreground">Subtitle</dt>
          <dd>{header.subtitle}</dd>
        </>
      )}
      {header.links.length > 0 && (
        <>
          <dt className="text-muted-foreground">Links</dt>
          <dd>{header.links.map((link) => link.label).join(" · ")}</dd>
        </>
      )}
      {header.cta && (
        <>
          <dt className="text-muted-foreground">Button</dt>
          <dd>{header.cta.label}</dd>
        </>
      )}
    </dl>
  )
}

const EMPTY_LINK: SiteHeaderLink = { label: "", href: "" }

function filled(link: SiteHeaderLink): boolean {
  return link.label.trim() !== "" || link.href.trim() !== ""
}

function HeaderForm({
  initial,
  busy,
  onCancel,
  onSave,
}: {
  initial: SiteHeader
  busy: boolean
  onCancel: () => void
  onSave: (header: SiteHeader) => void
}) {
  const [wordmark, setWordmark] = useState(initial.wordmark ?? "")
  const [subtitle, setSubtitle] = useState(initial.subtitle ?? "")
  const [links, setLinks] = useState<SiteHeaderLink[]>(
    Array.from({ length: MAX_LINKS }, (_, i) => initial.links[i] ?? EMPTY_LINK),
  )
  const [cta, setCta] = useState<SiteHeaderLink>(initial.cta ?? EMPTY_LINK)

  const setLink = (index: number, change: Partial<SiteHeaderLink>) =>
    setLinks((current) => current.map((link, i) => (i === index ? { ...link, ...change } : link)))

  return (
    <form
      className="mt-2 space-y-2"
      onSubmit={(event) => {
        event.preventDefault()
        onSave({
          wordmark: wordmark.trim() || null,
          subtitle: subtitle.trim() || null,
          links: links.filter(filled),
          cta: filled(cta) ? cta : null,
        })
      }}
    >
      <Field id="header-wordmark" label="Name" value={wordmark} onChange={setWordmark} />
      <Field id="header-subtitle" label="Subtitle" value={subtitle} onChange={setSubtitle} />
      {links.map((link, i) => (
        <LinkFields
          key={i}
          id={`header-link-${i + 1}`}
          label={`Link ${i + 1}`}
          link={link}
          onChange={(change) => setLink(i, change)}
        />
      ))}
      <LinkFields id="header-cta" label="Button" link={cta} onChange={(change) => setCta({ ...cta, ...change })} />
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={busy}>
          Save
        </Button>
        <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function Field({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  )
}

function LinkFields({
  id,
  label,
  link,
  onChange,
}: {
  id: string
  label: string
  link: SiteHeaderLink
  onChange: (change: Partial<SiteHeaderLink>) => void
}) {
  return (
    <fieldset className="grid grid-cols-2 gap-2">
      <legend className="sr-only">{label}</legend>
      <div className="grid gap-1">
        <Label htmlFor={`${id}-label`}>{label} text</Label>
        <Input id={`${id}-label`} value={link.label} onChange={(event) => onChange({ label: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor={`${id}-href`}>{label} address</Label>
        <Input
          id={`${id}-href`}
          value={link.href}
          placeholder="/about"
          onChange={(event) => onChange({ href: event.target.value })}
        />
      </div>
    </fieldset>
  )
}
