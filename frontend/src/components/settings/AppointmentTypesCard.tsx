// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ChevronDown, ChevronUp, Plus } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { SettingsCard, SettingsRow } from "@/components/settings/ui"
import { AppointmentTypeRow } from "./AppointmentTypeRow"
import {
  useAppointmentTypes,
  useCreateAppointmentType,
  useDeleteAppointmentType,
  useUpdateAppointmentType,
} from "@/hooks/useAppointmentTypes"
import { useSchedulingPolicy, useUpdateSchedulingPolicy } from "@/hooks/useSchedulingPolicy"
import type { AppointmentTypeResponse, UpdateAppointmentTypeRequest } from "@/types/scheduling"

const NOTICE_DEFAULT_OPTIONS = [
  { value: "2", label: "2 hours" },
  { value: "12", label: "12 hours" },
  { value: "24", label: "A day" },
  { value: "48", label: "2 days" },
  { value: "72", label: "3 days" },
  { value: "168", label: "A week" },
]

const CANCEL_OPTIONS = [
  { value: "24", label: "A day before" },
  { value: "48", label: "2 days before" },
  { value: "72", label: "3 days before" },
]

const RESCHEDULE_OPTIONS = [
  { value: "12", label: "12 hours before" },
  { value: "24", label: "A day before" },
  { value: "48", label: "2 days before" },
]

/** Defaults a brand-new appointment type is created with — a standard session. */
const NEW_TYPE_DEFAULTS = {
  name: "New type",
  duration_minutes: 50,
  audience: "existing" as const,
  earliest_offer_business_days: 1,
  horizon: 10,
  horizon_unit: "business" as const,
}

export function AppointmentTypesCard() {
  const { data: typesData } = useAppointmentTypes()
  const { data: policy } = useSchedulingPolicy()
  const createType = useCreateAppointmentType()
  const updateType = useUpdateAppointmentType()
  const deleteType = useDeleteAppointmentType()
  const updatePolicy = useUpdateSchedulingPolicy()

  const [openId, setOpenId] = useState<string | null>(null)
  const [defaultsOpen, setDefaultsOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AppointmentTypeResponse | null>(null)

  const types = typesData?.data ?? []
  const selfBookOn = Boolean(policy?.self_book_existing || policy?.self_book_new)
  const defaultNoticeHours = policy?.min_notice_hours ?? 24

  function handleAdd() {
    createType.mutate(NEW_TYPE_DEFAULTS, {
      onSuccess: (created) => setOpenId(created.id),
    })
  }

  function handleChange(id: string, patch: UpdateAppointmentTypeRequest) {
    updateType.mutate({ id, data: patch })
  }

  function handleConfirmDelete() {
    if (!deleteTarget) return
    deleteType.mutate(deleteTarget.id, {
      onSuccess: () => {
        if (openId === deleteTarget.id) setOpenId(null)
        setDeleteTarget(null)
      },
    })
  }

  return (
    <>
      <SettingsCard
        title="Appointment types"
        description={typesData?.migrated ? "Consultation and Intake were added for new patients; your existing types are unchanged." : undefined}
        flush
      >
        <div className="px-[22px] pt-1.5 pb-5">
          <ul className="m-0 list-none p-0">
            {types.map((t) => (
              <AppointmentTypeRow
                key={t.id}
                appointmentType={t}
                open={openId === t.id}
                onToggle={() => setOpenId(openId === t.id ? null : t.id)}
                onChange={(patch) => handleChange(t.id, patch)}
                onDelete={() => setDeleteTarget(t)}
                selfBookOn={selfBookOn}
                defaultNoticeHours={defaultNoticeHours}
              />
            ))}
          </ul>
          <div className="mt-2 flex items-center justify-between gap-3">
            <Button type="button" size="sm" onClick={handleAdd} disabled={createType.isPending}>
              <Plus className="h-4 w-4" />
              Add a type
            </Button>
            <button
              type="button"
              onClick={() => setDefaultsOpen(!defaultsOpen)}
              className="inline-flex items-center gap-1 border-0 bg-transparent text-[13px] font-semibold text-muted-foreground"
            >
              Defaults for all types
              {defaultsOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>

        {defaultsOpen && policy && (
          <div className="border-t border-border bg-foreground/[0.025]">
            <SettingsRow
              nested
              label="How much warning before any new booking"
              description="A type can ask for more in its own details."
            >
              <Select
                value={String(policy.min_notice_hours)}
                onValueChange={(v) => updatePolicy.mutate({ min_notice_hours: Number(v) })}
              >
                <SelectTrigger aria-label="Default notice">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {NOTICE_DEFAULT_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingsRow>
            <SettingsRow nested label="Patients may cancel until" description="Later than this, they have to message you.">
              <Select
                value={String(policy.cancel_cutoff_hours)}
                onValueChange={(v) => updatePolicy.mutate({ cancel_cutoff_hours: Number(v) })}
              >
                <SelectTrigger aria-label="Cancel cutoff">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CANCEL_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingsRow>
            <SettingsRow nested label="Patients may reschedule until">
              <Select
                value={String(policy.reschedule_cutoff_hours)}
                onValueChange={(v) => updatePolicy.mutate({ reschedule_cutoff_hours: Number(v) })}
              >
                <SelectTrigger aria-label="Reschedule cutoff">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RESCHEDULE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingsRow>
          </div>
        )}
      </SettingsCard>

      <Dialog open={deleteTarget != null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {deleteTarget?.name}?</DialogTitle>
            <DialogDescription>
              Appointments already booked as this type keep their existing time and label. New ones can no
              longer be offered or booked as it, including from any public booking page or draft that used
              it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDeleteTarget(null)} disabled={deleteType.isPending}>
              Cancel
            </Button>
            <Button type="button" variant="destructive" onClick={handleConfirmDelete} disabled={deleteType.isPending}>
              Delete type
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
