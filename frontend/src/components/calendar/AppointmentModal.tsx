// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { usePatientList } from "@/hooks/usePatients"
import {
  useCreateAppointment,
  useUpdateAppointment,
  useCancelAppointment,
} from "@/hooks/useAppointments"
import type { AppointmentResponse } from "@/types/scheduling"

interface AppointmentModalProps {
  open: boolean
  onClose: () => void
  defaultStart?: string
  defaultEnd?: string
  appointment?: AppointmentResponse | null
}

function toLocalDatetime(iso: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  const offset = d.getTimezoneOffset()
  const local = new Date(d.getTime() - offset * 60000)
  return local.toISOString().slice(0, 16)
}

function toUTC(localDatetime: string): string {
  if (!localDatetime) return ""
  return new Date(localDatetime).toISOString()
}

/**
 * Derive a stable key that changes whenever the modal should reinitialize.
 * This forces React to remount AppointmentForm with fresh initial state.
 */
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
  defaultEnd,
  appointment,
}: AppointmentModalProps) {
  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>
            {appointment ? "Edit Appointment" : "New Appointment"}
          </DialogTitle>
        </DialogHeader>
        {/* Key forces remount so useState initializers run fresh */}
        <AppointmentForm
          key={formKey(appointment, defaultStart)}
          appointment={appointment ?? null}
          defaultStart={defaultStart}
          defaultEnd={defaultEnd}
          onClose={onClose}
        />
      </DialogContent>
    </Dialog>
  )
}

function AppointmentForm({
  appointment,
  defaultStart,
  defaultEnd,
  onClose,
}: {
  appointment: AppointmentResponse | null
  defaultStart?: string
  defaultEnd?: string
  onClose: () => void
}) {
  const { data: patientData } = usePatientList()
  const patients = patientData?.data ?? []

  const createMutation = useCreateAppointment()
  const updateMutation = useUpdateAppointment()
  const cancelMutation = useCancelAppointment()

  const isEditing = !!appointment

  // Initialize from appointment (edit) or defaults (new)
  const [patientId, setPatientId] = useState(appointment?.patient_id ?? "")
  const [title, setTitle] = useState(appointment?.title ?? "")
  const [startAt, setStartAt] = useState(
    appointment ? toLocalDatetime(appointment.start_at) : defaultStart ? toLocalDatetime(defaultStart) : ""
  )
  const [endAt, setEndAt] = useState(
    appointment ? toLocalDatetime(appointment.end_at) : defaultEnd ? toLocalDatetime(defaultEnd) : ""
  )
  const [durationMinutes, setDurationMinutes] = useState(appointment?.duration_minutes ?? 50)
  const [sessionType, setSessionType] = useState(appointment?.session_type ?? "individual")
  const [videoLink, setVideoLink] = useState(appointment?.video_link ?? "")
  const [videoPlatform] = useState(appointment?.video_platform ?? "")
  const [notes, setNotes] = useState(appointment?.notes ?? "")

  const handleStartChange = (value: string) => {
    setStartAt(value)
    if (value && durationMinutes > 0) {
      const start = new Date(value)
      const end = new Date(start.getTime() + durationMinutes * 60000)
      const offset = end.getTimezoneOffset()
      const localEnd = new Date(end.getTime() - offset * 60000)
      setEndAt(localEnd.toISOString().slice(0, 16))
    }
  }

  const handleSubmit = () => {
    if (isEditing && appointment) {
      updateMutation.mutate(
        {
          appointmentId: appointment.id,
          data: {
            title,
            patient_id: patientId,
            start_at: toUTC(startAt),
            end_at: toUTC(endAt),
            duration_minutes: durationMinutes,
            session_type: sessionType,
            video_link: videoLink || null,
            video_platform: videoPlatform || null,
            notes: notes || null,
          },
        },
        { onSuccess: onClose }
      )
    } else {
      createMutation.mutate(
        {
          patient_id: patientId,
          title,
          start_at: toUTC(startAt),
          end_at: toUTC(endAt),
          duration_minutes: durationMinutes,
          session_type: sessionType,
          video_link: videoLink || null,
          video_platform: videoPlatform || null,
          notes: notes || null,
        },
        { onSuccess: onClose }
      )
    }
  }

  const handleCancel = () => {
    if (appointment) {
      cancelMutation.mutate(appointment.id, { onSuccess: onClose })
    }
  }

  const isSubmitting = createMutation.isPending || updateMutation.isPending

  return (
    <>
      <div className="grid gap-4 py-4">
        <div className="grid gap-2">
          <Label htmlFor="patient">Patient</Label>
          <Select value={patientId} onValueChange={setPatientId}>
            <SelectTrigger id="patient">
              <SelectValue placeholder="Select patient" />
            </SelectTrigger>
            <SelectContent>
              {patients.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.last_name}, {p.first_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="title">Title</Label>
          <Input
            id="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Session with patient"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="grid gap-2">
            <Label htmlFor="start">Start</Label>
            <Input
              id="start"
              type="datetime-local"
              value={startAt}
              onChange={(e) => handleStartChange(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="duration">Duration (min)</Label>
            <Input
              id="duration"
              type="number"
              min={1}
              max={480}
              value={durationMinutes}
              onChange={(e) => setDurationMinutes(Number(e.target.value))}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="grid gap-2">
            <Label htmlFor="session-type">Session Type</Label>
            <Select value={sessionType} onValueChange={setSessionType}>
              <SelectTrigger id="session-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="individual">Individual</SelectItem>
                <SelectItem value="couples">Couples</SelectItem>
                <SelectItem value="group">Group</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="video-link">Video Link</Label>
            <Input
              id="video-link"
              value={videoLink}
              onChange={(e) => setVideoLink(e.target.value)}
              placeholder="https://zoom.us/j/..."
            />
          </div>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="notes">Notes</Label>
          <Textarea
            id="notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
          />
        </div>
      </div>

      <DialogFooter className="flex justify-between">
        {isEditing && appointment?.status !== "cancelled" && (
          <Button
            variant="destructive"
            onClick={handleCancel}
            disabled={cancelMutation.isPending}
          >
            Cancel Appointment
          </Button>
        )}
        <div className="flex gap-2 ml-auto">
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button onClick={handleSubmit} disabled={!patientId || !startAt || isSubmitting}>
            {isEditing ? "Save Changes" : "Create"}
          </Button>
        </div>
      </DialogFooter>
    </>
  )
}
