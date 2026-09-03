// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecordOutcomeMeasureButton
 *
 * Patient-scoped entry point for manually recording a scored instrument
 * (PHQ-9 / GAD-7). The clinician either fills in per-item responses (0–3,
 * auto-totaled) or enters a known total when item detail isn't available,
 * picks the date administered, and POSTs with `source: "manual"`.
 *
 * Scoring, validation, and severity are owned by the backend — this form only
 * collects responses and renders what comes back. The PHQ-9 item-9 safety
 * signal is surfaced non-blockingly: a live warning while filling, and a
 * persistent indicator on the saved row (see OutcomeMeasureTrend).
 */

"use client"

import { useState } from "react"
import { AlertTriangle, Plus } from "lucide-react"
import { ApiError } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { useToast } from "@/components/ui/Toast"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { useCreateOutcomeMeasure } from "@/hooks/useOutcomeMeasures"
import {
  INSTRUMENTS,
  getInstrumentMeta,
  tripsSafetySignal,
  type InstrumentMeta,
} from "@/lib/outcomeMeasures"

interface RecordOutcomeMeasureButtonProps {
  patientId: string
}

type EntryMode = "items" | "total"

/** "YYYY-MM-DDTHH:mm" for a datetime-local default of "now". */
function nowLocalInput(): string {
  const now = new Date()
  const offsetMs = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 16)
}

/** Map a known backend error code to a clinician-facing message. */
function messageForError(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.code) {
      case "UNKNOWN_INSTRUMENT":
        return "That instrument isn't recognized. Pick PHQ-9 or GAD-7."
      case "INVALID_ITEM_SCORES":
        return "One or more item responses are out of range."
      case "INVALID_REQUEST":
        return "Enter at least one item response or a total score."
      case "NOT_FOUND":
        return "You don't have access to this patient."
      default:
        return err.message
    }
  }
  return "Could not save the score. Please try again."
}

export function RecordOutcomeMeasureButton({
  patientId,
}: RecordOutcomeMeasureButtonProps) {
  const [open, setOpen] = useState(false)
  const [instrumentCode, setInstrumentCode] = useState(INSTRUMENTS[0].code)
  const [mode, setMode] = useState<EntryMode>("items")
  const [itemScores, setItemScores] = useState<Record<string, number>>({})
  const [totalInput, setTotalInput] = useState("")
  const [administeredAt, setAdministeredAt] = useState(nowLocalInput)

  const { showToast } = useToast()
  const createMeasure = useCreateOutcomeMeasure()
  const { readOnly } = useReadOnlyMode()

  const meta = getInstrumentMeta(instrumentCode) as InstrumentMeta
  const answeredKeys = Object.keys(itemScores)
  const runningTotal = answeredKeys.reduce((sum, k) => sum + itemScores[k], 0)
  const safetyTripped =
    mode === "items" && tripsSafetySignal(meta, itemScores)

  const parsedTotal = totalInput.trim() === "" ? null : Number(totalInput)
  const totalValid =
    parsedTotal !== null && Number.isInteger(parsedTotal) && parsedTotal >= 0

  const canSubmit =
    !createMeasure.isPending &&
    (mode === "items" ? answeredKeys.length > 0 : totalValid)

  function resetForInstrument(code: string) {
    setInstrumentCode(code)
    setItemScores({})
    setTotalInput("")
  }

  function reset() {
    resetForInstrument(INSTRUMENTS[0].code)
    setMode("items")
    setAdministeredAt(nowLocalInput())
  }

  function setItem(key: string, value: number) {
    setItemScores((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSubmit() {
    if (!canSubmit) return
    try {
      await createMeasure.mutateAsync({
        patientId,
        data: {
          instrument: instrumentCode,
          source: "manual",
          administered_at: new Date(administeredAt).toISOString(),
          ...(mode === "items"
            ? { item_scores: itemScores }
            : { total_score: parsedTotal as number }),
        },
      })
      showToast("Score recorded.", "success")
      setOpen(false)
      reset()
    } catch (err) {
      showToast(messageForError(err), "error")
    }
  }

  if (readOnly) return null

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="w-4 h-4 mr-2" />
          Record score
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Record outcome measure</DialogTitle>
          <DialogDescription>
            Enter a standardized instrument score for this patient. Scoring and
            severity are calculated automatically.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 pt-1">
          {/* Instrument picker */}
          <div className="space-y-1.5">
            <Label>Instrument</Label>
            <div className="flex gap-2">
              {INSTRUMENTS.map((inst) => (
                <button
                  key={inst.code}
                  type="button"
                  onClick={() => resetForInstrument(inst.code)}
                  className={
                    inst.code === instrumentCode
                      ? "rounded-md border border-primary-400 bg-primary-50 px-3 py-1.5 text-sm font-medium text-primary-800"
                      : "rounded-md border border-neutral-200 px-3 py-1.5 text-sm text-neutral-700 hover:border-neutral-300"
                  }
                >
                  {inst.displayName}
                </button>
              ))}
            </div>
          </div>

          {/* Date administered */}
          <div className="space-y-1.5">
            <Label htmlFor="administered-at">Date administered</Label>
            <Input
              id="administered-at"
              type="datetime-local"
              value={administeredAt}
              onChange={(e) => setAdministeredAt(e.target.value)}
              className="w-fit"
            />
          </div>

          {/* Entry-mode toggle */}
          <div className="flex gap-2 text-sm">
            <button
              type="button"
              onClick={() => setMode("items")}
              className={
                mode === "items"
                  ? "rounded-md bg-primary px-3 py-1 font-medium text-primary-foreground"
                  : "rounded-md px-3 py-1 text-muted-foreground hover:bg-foreground/5"
              }
            >
              Per-item
            </button>
            <button
              type="button"
              onClick={() => setMode("total")}
              className={
                mode === "total"
                  ? "rounded-md bg-primary px-3 py-1 font-medium text-primary-foreground"
                  : "rounded-md px-3 py-1 text-muted-foreground hover:bg-foreground/5"
              }
            >
              Known total
            </button>
          </div>

          {mode === "items" ? (
            <div className="space-y-3">
              {meta.items.map((prompt, idx) => {
                const key = String(idx + 1)
                const selected = itemScores[key]
                const isSafetyItem = meta.safetySignal?.itemKey === key
                return (
                  <div key={key} className="space-y-1.5">
                    <div className="flex gap-2 text-sm text-neutral-800">
                      <span className="font-medium text-neutral-400">
                        {key}.
                      </span>
                      <span>{prompt}</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 pl-5">
                      {meta.responseOptions.map((opt) => {
                        const active = selected === opt.value
                        return (
                          <button
                            key={opt.value}
                            type="button"
                            aria-pressed={active}
                            onClick={() => setItem(key, opt.value)}
                            className={
                              active
                                ? "rounded-md border border-primary-400 bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-800"
                                : "rounded-md border border-neutral-200 px-2.5 py-1 text-xs text-neutral-600 hover:border-neutral-300"
                            }
                          >
                            {opt.value} · {opt.label}
                          </button>
                        )
                      })}
                    </div>
                    {isSafetyItem &&
                      selected !== undefined &&
                      selected >= (meta.safetySignal?.threshold ?? 1) && (
                        <p className="ml-5 text-xs font-medium text-amber-700">
                          {meta.safetySignal?.label}
                        </p>
                      )}
                  </div>
                )
              })}
              <div className="flex items-center justify-between rounded-md bg-neutral-50 px-3 py-2 text-sm">
                <span className="text-neutral-600">
                  Total ({answeredKeys.length}/{meta.items.length} answered)
                </span>
                <span className="font-semibold text-neutral-900">
                  {runningTotal}
                </span>
              </div>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="total-score">Total score</Label>
              <Input
                id="total-score"
                type="number"
                min={0}
                inputMode="numeric"
                value={totalInput}
                onChange={(e) => setTotalInput(e.target.value)}
                placeholder="e.g. 14"
                className="w-32"
              />
              <p className="text-xs text-neutral-500">
                Use this when you only have the summary score, not item-level
                responses.
              </p>
            </div>
          )}

          {/* Non-blocking safety callout */}
          {safetyTripped && meta.safetySignal && (
            <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 p-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="space-y-0.5">
                <p className="text-sm font-medium text-amber-800">
                  {meta.safetySignal.label}
                </p>
                <p className="text-xs text-amber-700">
                  {meta.safetySignal.guidance}
                </p>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              setOpen(false)
              reset()
            }}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            {createMeasure.isPending ? "Saving…" : "Save score"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
