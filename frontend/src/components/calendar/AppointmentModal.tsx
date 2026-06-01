// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo, useRef, useState } from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { Check, ChevronRight, Plus, X } from "lucide-react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { usePatientList } from "@/hooks/usePatients"
import {
  useCreateAppointment,
  useUpdateAppointment,
  useCancelAppointment,
} from "@/hooks/useAppointments"
import { useNoteTypes } from "@/hooks/useNoteTypes"
import type { AppointmentResponse, SessionType } from "@/types/scheduling"
import type { PatientResponse } from "@/types/patients"
import type { UserPreferences } from "@/lib/api/users"
import { DEFAULT_NOTE_TYPE } from "@/types/noteTypes"
import type { EditorialTheme } from "./editorial/EditorialSidebar"
import "./editorial/editorial.css"

const SESSION_TYPES: { value: SessionType; label: string }[] = [
  { value: "individual", label: "Individual" },
  { value: "couples", label: "Couples" },
  { value: "group", label: "Group" },
]
const SESSION_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  SESSION_TYPES.map((s) => [s.value, s.label]),
)
const QUICK_LENGTHS = [45, 50, 30, 60, 90]

function buildTitle(patient: PatientResponse | undefined, sessionType: string): string {
  if (!patient) return ""
  const label = SESSION_TYPE_LABELS[sessionType] ?? sessionType
  return `${patient.first_name} ${patient.last_name} — ${label}`
}

function toDateInput(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`
}

function toTimeInput(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

function fromInputs(dateStr: string, timeStr: string): Date {
  const [y, mo, da] = dateStr.split("-").map(Number)
  const [h, mi] = timeStr.split(":").map(Number)
  return new Date(y, mo - 1, da, h, mi, 0, 0)
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
}

function formatDay(d: Date): string {
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
}

type Layout = "dialog" | "sheet"

interface AppointmentModalProps {
  open: boolean
  onClose: () => void
  defaultStart?: string
  appointment?: AppointmentResponse | null
  preferences?: UserPreferences
  layout?: Layout
  theme?: EditorialTheme
}

function formKey(
  appointment: AppointmentResponse | null | undefined,
  defaultStart?: string,
): string {
  if (appointment) return `edit-${appointment.id}-${appointment.updated_at}`
  return `new-${defaultStart ?? "empty"}`
}

export function AppointmentModal({
  open,
  onClose,
  defaultStart,
  appointment,
  preferences,
  layout = "dialog",
  theme = "light",
}: AppointmentModalProps) {
  const isSheet = layout === "sheet"
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className="fixed inset-0 z-50 bg-[rgba(28,18,12,0.34)] backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0"
        />
        <DialogPrimitive.Content
          data-editorial-theme={theme}
          aria-describedby={undefined}
          className={
            isSheet
              ? "ed-sheet-in fixed inset-y-0 right-0 z-50 flex h-full w-[420px] max-w-full flex-col overflow-hidden border-l outline-none"
              : "ed-dialog-in fixed left-1/2 top-1/2 z-50 flex w-[460px] max-w-[calc(100%-2rem)] max-h-[94%] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden border outline-none"
          }
          style={{
            backgroundColor: "var(--ed-canvas-elev)",
            color: "var(--ed-ink)",
            boxShadow: "var(--ed-shadow-modal)",
            borderColor: "var(--ed-hairline-strong)",
            borderRadius: isSheet ? 0 : "calc(var(--radius) + 8px)",
          }}
        >
          <AppointmentForm
            key={formKey(appointment, defaultStart)}
            appointment={appointment ?? null}
            defaultStart={defaultStart}
            onClose={onClose}
            preferences={preferences}
          />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

function FieldLabel({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between">
      <span
        className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-[0.13em]"
        style={{ color: "var(--ed-ink-soft)" }}
      >
        {children}
      </span>
      {hint && (
        <span className="text-[11.5px]" style={{ color: "var(--ed-ink-soft)" }}>
          {hint}
        </span>
      )}
    </div>
  )
}

const FIELD_CLASS =
  "w-full box-border rounded-[10px] border bg-transparent px-3 py-2.5 text-sm outline-none"

function fieldStyle(): React.CSSProperties {
  return {
    borderColor: "var(--ed-field-border)",
    backgroundColor: "var(--ed-field-bg)",
    color: "var(--ed-ink)",
  }
}

function AppointmentForm({
  appointment,
  defaultStart,
  onClose,
  preferences,
}: {
  appointment: AppointmentResponse | null
  defaultStart?: string
  onClose: () => void
  preferences?: UserPreferences
}) {
  const { data: patientData } = usePatientList()
  const patients = patientData?.data ?? []
  const { data: noteTypesData } = useNoteTypes()
  const noteTypes = noteTypesData?.note_types ?? []

  const createMutation = useCreateAppointment()
  const updateMutation = useUpdateAppointment()
  const cancelMutation = useCancelAppointment()

  const isEditing = !!appointment

  const defaultDuration =
    appointment?.duration_minutes ?? preferences?.default_duration_minutes ?? 45
  const defaultSessionType =
    appointment?.session_type ?? preferences?.default_session_type ?? "individual"

  const start0 = useMemo(() => {
    if (appointment) return new Date(appointment.start_at)
    if (defaultStart) return new Date(defaultStart)
    const d = new Date()
    d.setMinutes(0, 0, 0)
    d.setHours(d.getHours() + 1)
    return d
  }, [appointment, defaultStart])

  const [patientId, setPatientId] = useState(appointment?.patient_id ?? "")
  const [dateStr, setDateStr] = useState(toDateInput(start0))
  const [timeStr, setTimeStr] = useState(toTimeInput(start0))
  const [duration, setDuration] = useState(defaultDuration)
  const [sessionType, setSessionType] = useState(defaultSessionType)

  const [lengths, setLengths] = useState<number[]>(() => {
    const base = [...QUICK_LENGTHS]
    if (appointment && !base.includes(appointment.duration_minutes)) {
      base.push(appointment.duration_minutes)
    }
    return base.sort((a, b) => a - b)
  })
  const [addingLen, setAddingLen] = useState(false)
  const [newLen, setNewLen] = useState("")

  // null = title tracks patient/type automatically; a string = user override.
  const [titleOverride, setTitleOverride] = useState<string | null>(
    appointment?.title ?? null,
  )
  const [editingTitle, setEditingTitle] = useState(false)

  const [moreOpen, setMoreOpen] = useState(
    isEditing && (!!appointment.video_link || !!appointment.notes),
  )
  const [videoLink, setVideoLink] = useState(appointment?.video_link ?? "")
  const [noteType, setNoteType] = useState<string>(DEFAULT_NOTE_TYPE)
  const [notes, setNotes] = useState(appointment?.notes ?? "")

  const newLenRef = useRef<HTMLInputElement>(null)

  const patient = patients.find((p) => p.id === patientId)
  const start = fromInputs(dateStr, timeStr)
  const end = new Date(start.getTime() + duration * 60000)
  const computedTitle = buildTitle(patient, sessionType)
  // An empty/whitespace override means "no override" — the caption and the
  // submitted payload fall back to the auto title.
  const title = titleOverride?.trim() ? titleOverride : computedTitle
  // The inline editor binds to the raw override so it can be cleared and
  // retyped; a null override (auto mode) seeds it with the computed title.
  const titleInputValue = titleOverride ?? computedTitle

  const addLength = () => {
    const v = parseInt(newLen, 10)
    if (v && v > 0 && !lengths.includes(v)) {
      setLengths((prev) => [...prev, v].sort((a, b) => a - b))
      setDuration(v)
    }
    setNewLen("")
    setAddingLen(false)
  }

  const canSave = !!patientId
  const isSubmitting = createMutation.isPending || updateMutation.isPending

  const handleSubmit = () => {
    const payload = {
      patient_id: patientId,
      title,
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      duration_minutes: duration,
      session_type: sessionType,
      video_link: videoLink || null,
      notes: notes || null,
    }
    if (isEditing && appointment) {
      updateMutation.mutate(
        { appointmentId: appointment.id, data: payload },
        { onSuccess: onClose },
      )
    } else {
      createMutation.mutate(payload, { onSuccess: onClose })
    }
  }

  const handleCancelAppt = () => {
    if (appointment) {
      cancelMutation.mutate(appointment.id, { onSuccess: onClose })
    }
  }

  const headerHint = `${formatDay(start)} · ${formatTime(start)}`

  return (
    <>
      {/* Header */}
      <div
        className="flex items-center justify-between px-[22px] pb-3.5 pt-[18px]"
        style={{ borderBottom: "1px solid var(--ed-hairline)" }}
      >
        <div>
          <DialogPrimitive.Title
            className="font-display m-0 text-[21px] font-semibold tracking-[-0.01em]"
            style={{ color: "var(--ed-ink)" }}
          >
            {isEditing ? "Edit appointment" : "New appointment"}
          </DialogPrimitive.Title>
          <p className="mt-0.5 text-[13px]" style={{ color: "var(--ed-ink-soft)" }}>
            {headerHint}
          </p>
        </div>
        <button
          type="button"
          className="ed-iconbtn p-[7px]"
          onClick={onClose}
          aria-label="Close"
          style={{ color: "var(--ed-ink-muted)" }}
        >
          <X size={18} />
        </button>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-[22px] py-[18px]">
        {/* Patient */}
        <div>
          <FieldLabel>Patient</FieldLabel>
          <Select value={patientId} onValueChange={setPatientId}>
            <SelectTrigger
              id="patient"
              aria-label="Patient"
              aria-required="true"
              className="w-full"
            >
              <SelectValue placeholder="Select patient…" />
            </SelectTrigger>
            <SelectContent>
              {patients.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.last_name}, {p.first_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Auto title — subtle, editable caption */}
          {patient && (
            <div className="mt-2 flex items-center gap-2 text-[12.5px]">
              {editingTitle ? (
                <Input
                  autoFocus
                  value={titleInputValue}
                  aria-label="Title"
                  onChange={(e) => setTitleOverride(e.target.value)}
                  onBlur={() => setEditingTitle(false)}
                  className="h-auto py-1.5 text-[13px]"
                />
              ) : (
                <>
                  <span style={{ color: "var(--ed-ink-soft)" }}>
                    Title:{" "}
                    <span className="font-medium" style={{ color: "var(--ed-ink-muted)" }}>
                      {title}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => setEditingTitle(true)}
                    className="cursor-pointer border-none bg-transparent p-0 text-[12.5px] font-semibold"
                    style={{ color: "var(--ed-accent)" }}
                  >
                    Edit
                  </button>
                  {titleOverride != null && (
                    <button
                      type="button"
                      onClick={() => setTitleOverride(null)}
                      className="cursor-pointer border-none bg-transparent p-0 text-[12.5px]"
                      style={{ color: "var(--ed-ink-soft)" }}
                    >
                      reset
                    </button>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* When */}
        <div>
          <FieldLabel hint={`Ends ${formatTime(end)}`}>When</FieldLabel>
          <div className="flex gap-2">
            <input
              type="date"
              aria-label="Date"
              value={dateStr}
              onChange={(e) => setDateStr(e.target.value)}
              className={`${FIELD_CLASS} flex-[1.4]`}
              style={fieldStyle()}
            />
            <input
              type="time"
              aria-label="Time"
              step={900}
              value={timeStr}
              onChange={(e) => setTimeStr(e.target.value)}
              className={`${FIELD_CLASS} flex-1`}
              style={fieldStyle()}
            />
          </div>
        </div>

        {/* Length — quick-pick chips */}
        <div>
          <FieldLabel>Length</FieldLabel>
          <div className="flex flex-wrap gap-[7px]">
            {lengths.map((m) => {
              const active = duration === m
              return (
                <button
                  key={m}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setDuration(m)}
                  className="inline-flex cursor-pointer items-baseline gap-[3px] rounded-full border px-3.5 py-[7px] text-[13.5px] font-semibold"
                  style={{
                    borderColor: active ? "var(--ed-cta-bg)" : "var(--ed-field-border)",
                    backgroundColor: active ? "var(--ed-cta-bg)" : "transparent",
                    color: active ? "var(--ed-cta-fg)" : "var(--ed-ink-muted)",
                  }}
                >
                  {m}
                  <span className="text-[11px] font-medium opacity-75">min</span>
                </button>
              )
            })}
            {addingLen ? (
              <span
                className="inline-flex items-center gap-1 rounded-full py-[3px] pl-3 pr-1"
                style={{ border: "1px solid var(--ed-cta-bg)" }}
              >
                <input
                  ref={newLenRef}
                  autoFocus
                  type="number"
                  min={5}
                  max={240}
                  value={newLen}
                  placeholder="min"
                  aria-label="Custom length"
                  onChange={(e) => setNewLen(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addLength()
                    if (e.key === "Escape") {
                      setAddingLen(false)
                      setNewLen("")
                    }
                  }}
                  className="w-[46px] border-none bg-transparent text-[13.5px] font-semibold outline-none"
                  style={{ color: "var(--ed-ink)" }}
                />
                <button
                  type="button"
                  onClick={addLength}
                  className="ed-iconbtn p-[5px]"
                  aria-label="Add length"
                  style={{
                    backgroundColor: "var(--ed-cta-bg)",
                    color: "var(--ed-cta-fg)",
                  }}
                >
                  <Check size={13} strokeWidth={3} />
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setAddingLen(true)}
                title="Add a custom length"
                className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-dashed bg-transparent px-[13px] py-[7px] text-[13.5px] font-semibold"
                style={{ borderColor: "var(--ed-field-border)", color: "var(--ed-ink-soft)" }}
              >
                <Plus size={13} strokeWidth={2.6} /> Add
              </button>
            )}
          </div>
        </div>

        {/* Session type — segmented control */}
        <div>
          <FieldLabel>Session type</FieldLabel>
          <div
            className="inline-flex gap-0.5 rounded-[10px] border p-[3px]"
            role="radiogroup"
            aria-label="Session type"
            style={{
              borderColor: "var(--ed-field-border)",
              backgroundColor: "var(--ed-field-bg)",
            }}
          >
            {SESSION_TYPES.map((s) => {
              const active = sessionType === s.value
              return (
                <button
                  key={s.value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setSessionType(s.value)}
                  className="cursor-pointer rounded-[7px] border-none px-4 py-[7px] text-[13.5px] font-semibold"
                  style={{
                    backgroundColor: active ? "var(--ed-cta-bg)" : "transparent",
                    color: active ? "var(--ed-cta-fg)" : "var(--ed-ink-muted)",
                  }}
                >
                  {s.label}
                </button>
              )
            })}
          </div>
        </div>

        {/* More options */}
        <div className="pt-3.5" style={{ borderTop: "1px solid var(--ed-hairline)" }}>
          <button
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            aria-expanded={moreOpen}
            className="flex cursor-pointer items-center gap-1.5 border-none bg-transparent p-0 text-[13px] font-semibold"
            style={{ color: "var(--ed-ink-muted)" }}
          >
            <ChevronRight
              size={15}
              className="transition-transform duration-150"
              style={{ transform: moreOpen ? "rotate(90deg)" : "none" }}
            />
            More options
            <span className="text-[12px] font-medium" style={{ color: "var(--ed-ink-soft)" }}>
              · video link, note type, notes
            </span>
          </button>

          {moreOpen && (
            <div className="ed-fade-in mt-4 flex flex-col gap-[18px]">
              <div>
                <FieldLabel>Video link</FieldLabel>
                <input
                  value={videoLink}
                  aria-label="Video link"
                  onChange={(e) => setVideoLink(e.target.value)}
                  placeholder="https://zoom.us/j/…"
                  className={FIELD_CLASS}
                  style={fieldStyle()}
                />
              </div>
              <div>
                <FieldLabel hint="Used when you start the session">Note type</FieldLabel>
                <Select value={noteType} onValueChange={setNoteType}>
                  <SelectTrigger id="note-type" aria-label="Note type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {noteTypes.length === 0 ? (
                      <SelectItem value={DEFAULT_NOTE_TYPE}>SOAP</SelectItem>
                    ) : (
                      noteTypes.map((nt) => (
                        <SelectItem key={nt.key} value={nt.key}>
                          {nt.label}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <FieldLabel>Notes</FieldLabel>
                <Textarea
                  value={notes}
                  aria-label="Notes"
                  onChange={(e) => setNotes(e.target.value)}
                  rows={3}
                  className="resize-y leading-relaxed"
                  style={fieldStyle()}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div
        className="flex items-center justify-end gap-2.5 px-[22px] py-3.5"
        style={{ borderTop: "1px solid var(--ed-hairline)" }}
      >
        {isEditing && appointment.status !== "cancelled" && (
          <button
            type="button"
            onClick={handleCancelAppt}
            disabled={cancelMutation.isPending}
            className="mr-auto cursor-pointer rounded-full border bg-transparent px-4 py-[9px] text-[13px] font-semibold disabled:opacity-50"
            style={{
              borderColor: "var(--ed-status-noshow-fg)",
              color: "var(--ed-status-noshow-fg)",
            }}
          >
            Cancel appointment
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          className="cursor-pointer rounded-full border bg-transparent px-[18px] py-[9px] text-[13.5px] font-semibold"
          style={{ borderColor: "var(--ed-field-border)", color: "var(--ed-ink-muted)" }}
        >
          {isEditing ? "Close" : "Cancel"}
        </button>
        <button
          type="button"
          disabled={!canSave || isSubmitting}
          onClick={handleSubmit}
          className="cursor-pointer rounded-full border-none px-[22px] py-[9px] text-[13.5px] font-bold disabled:cursor-not-allowed disabled:opacity-50"
          style={{ backgroundColor: "var(--ed-cta-bg)", color: "var(--ed-cta-fg)" }}
        >
          {isEditing ? "Save changes" : "Schedule"}
        </button>
      </div>
    </>
  )
}
