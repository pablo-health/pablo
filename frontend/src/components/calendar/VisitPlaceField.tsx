// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where a session happens, and which link a video session gets.
 *
 * One In person / Video switch on the appointment form. With Video, one line
 * says what the save will do: the clinician's default service makes the
 * link, or there is a field to paste one. "Change" offers only the services
 * this clinician can use right now (GET /telehealth/providers, `connected`),
 * so the form never promises a room that would not arrive.
 *
 * The state lives in the form; this file holds the rules for reading an
 * appointment into it and the field itself. What the save sends is decided in
 * the form, beside the rest of the payload.
 */

"use client"

import Link from "next/link"
import { Video } from "lucide-react"
import { CALENDAR_SETUP_PATH } from "./connect/CalendarSetupWizard"
import type { TelehealthProvider, TelehealthProviders } from "@/lib/api/telehealth"
import type { AppointmentResponse } from "@/types/scheduling"

export type VisitPlace = "in_person" | "video"
export type PlaceCode = "11" | "02" | "10"

/** The office code, the only in-person code the form writes. */
export const IN_PERSON_CODE: PlaceCode = "11"
/**
 * The telehealth code a first video booking takes, the same one an
 * appointment type maps "video" onto. A clinician who codes 10 keeps 10: an
 * existing code, or the last one they booked with, is always reused.
 */
export const VIDEO_CODE: PlaceCode = "02"

/** The provider id for a link the clinician pastes. */
export const MANUAL = "manual"
const GOOGLE_MEET = "google_meet"
const ZOOM = "zoom"

export function placeOfCode(code: string | null | undefined): VisitPlace | null {
  if (code === "11") return "in_person"
  if (code === "02" || code === "10") return "video"
  return null
}

/** A link the clinician typed, as the server tells them apart. */
export function holdsPastedLink(
  appointment: Pick<AppointmentResponse, "provider" | "video_link">,
): boolean {
  return (
    appointment.provider === MANUAL || Boolean(appointment.video_link && !appointment.provider)
  )
}

/** Where an existing appointment happens, read from what it already says. */
export function placeOfAppointment(appointment: AppointmentResponse): VisitPlace | null {
  const coded = placeOfCode(appointment.place_of_service)
  if (coded) return coded
  if (appointment.video_link || appointment.provider) return "video"
  return null
}

/** The services that make a link on their own: connected, and not "paste". */
export function linkServices(data: TelehealthProviders | undefined): TelehealthProvider[] {
  return (data?.providers ?? []).filter((p) => p.connected && p.id !== MANUAL)
}

export function serviceName(
  data: TelehealthProviders | undefined,
  providerId: string | null | undefined,
): string | null {
  if (!providerId || providerId === MANUAL) return null
  return data?.providers.find((p) => p.id === providerId)?.display_name ?? null
}

/** A room the appointment already has, and who made it. */
export interface ExistingRoom {
  link: string
  provider: string | null
}

const SEGMENTS: { value: VisitPlace; label: string }[] = [
  { value: "in_person", label: "In person" },
  { value: "video", label: "Video" },
]

const LINK_BUTTON =
  "cursor-pointer border-none bg-transparent p-0 text-[12.5px] font-semibold"

function ChangeButton({ onClick, open }: { onClick: () => void; open: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      className={LINK_BUTTON}
      style={{ color: "var(--ed-accent)" }}
    >
      Change
    </button>
  )
}

export interface VisitPlaceFieldProps {
  place: VisitPlace | null
  onPlaceChange: (place: VisitPlace) => void
  providers: TelehealthProviders | undefined
  /** The service the save will ask for, or null when the form pastes. */
  service: TelehealthProvider | null
  /** A room this appointment already holds and the form is keeping. */
  room: ExistingRoom | null
  pasting: boolean
  videoLink: string
  onVideoLinkChange: (link: string) => void
  onChooseService: (providerId: string) => void
  onChoosePaste: () => void
  onConnectZoom: () => void
  /** Only a pasted link can be written here: a series, or a link already pasted. */
  pasteOnly: boolean
  /** An existing appointment with no place and no link: offer one tap. */
  onAddRoom?: () => void
  addingRoom?: boolean
  changeOpen: boolean
  onToggleChange: () => void
  fieldClass: string
  fieldStyle: React.CSSProperties
}

export function VisitPlaceField({
  place,
  onPlaceChange,
  providers,
  service,
  room,
  pasting,
  videoLink,
  onVideoLinkChange,
  onChooseService,
  onChoosePaste,
  onConnectZoom,
  pasteOnly,
  onAddRoom,
  addingRoom = false,
  changeOpen,
  onToggleChange,
  fieldClass,
  fieldStyle,
}: VisitPlaceFieldProps) {
  const services = pasteOnly ? [] : linkServices(providers)
  const meet = providers?.providers.find((p) => p.id === GOOGLE_MEET)
  const zoom = providers?.providers.find((p) => p.id === ZOOM)
  const offerZoomConnect = !pasteOnly && Boolean(providers?.zoom_app) && zoom?.connected === false
  const offerMeetConnect = !pasteOnly && services.length === 0 && meet?.connected === false
  const canChange = services.length > 0 || offerZoomConnect
  const defaultService =
    services.find((p) => p.id === providers?.default_provider) ?? null

  return (
    <div data-testid="visit-place">
      <div
        className="inline-flex gap-0.5 rounded-[10px] border p-[3px]"
        role="radiogroup"
        aria-label="Where"
        style={{ borderColor: "var(--ed-field-border)", backgroundColor: "var(--ed-field-bg)" }}
      >
        {SEGMENTS.map((s) => {
          const active = place === s.value
          return (
            <button
              key={s.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onPlaceChange(s.value)}
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

      {place === null && onAddRoom && (
        <p className="mt-2 flex items-center gap-1.5 text-[12.5px]" style={{ color: "var(--ed-ink-soft)" }}>
          No video link ·{" "}
          <button
            type="button"
            onClick={onAddRoom}
            disabled={addingRoom}
            className={LINK_BUTTON}
            style={{ color: "var(--ed-accent)" }}
          >
            {defaultService ? `Add ${defaultService.display_name}` : "Add a link"}
          </button>
        </p>
      )}

      {place === "video" && (
        <div className="mt-2 flex flex-col gap-2 text-[12.5px]" style={{ color: "var(--ed-ink-soft)" }}>
          {room && !pasting ? (
            <p className="flex min-w-0 items-center gap-1.5" data-testid="video-room">
              <Video size={14} aria-hidden="true" className="shrink-0" />
              {serviceName(providers, room.provider) && (
                <span className="shrink-0 font-semibold" style={{ color: "var(--ed-ink-muted)" }}>
                  {serviceName(providers, room.provider)}
                </span>
              )}
              <a
                href={room.link}
                target="_blank"
                rel="noreferrer"
                className="truncate"
                style={{ color: "var(--ed-ink-muted)" }}
              >
                {room.link}
              </a>
              {canChange && (
                <>
                  <span aria-hidden="true">·</span>
                  <ChangeButton onClick={onToggleChange} open={changeOpen} />
                </>
              )}
            </p>
          ) : service && !pasting ? (
            <p className="flex items-center gap-1.5" data-testid="video-link-promise">
              <Video size={14} aria-hidden="true" />
              {service.display_name} link added when you save ·
              <ChangeButton onClick={onToggleChange} open={changeOpen} />
            </p>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <input
                  value={videoLink}
                  aria-label="Video link"
                  onChange={(e) => onVideoLinkChange(e.target.value)}
                  placeholder="Paste a link"
                  className={fieldClass}
                  style={fieldStyle}
                />
                {canChange && <ChangeButton onClick={onToggleChange} open={changeOpen} />}
              </div>
              {offerMeetConnect && (
                <p>
                  <Link
                    href={CALENDAR_SETUP_PATH}
                    className="font-semibold"
                    style={{ color: "var(--ed-accent)" }}
                  >
                    Connect Google Calendar
                  </Link>{" "}
                  to get Meet links automatically.
                </p>
              )}
            </>
          )}

          {changeOpen && canChange && (
            <ul aria-label="Video service" className="flex flex-col gap-1">
              {services.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => onChooseService(p.id)}
                    aria-current={!pasting && service?.id === p.id ? "true" : undefined}
                    className={LINK_BUTTON}
                    style={{ color: "var(--ed-ink-muted)" }}
                  >
                    {p.display_name}
                  </button>
                </li>
              ))}
              <li>
                <button
                  type="button"
                  onClick={onChoosePaste}
                  aria-current={pasting ? "true" : undefined}
                  className={LINK_BUTTON}
                  style={{ color: "var(--ed-ink-muted)" }}
                >
                  Paste a link
                </button>
              </li>
              {offerZoomConnect && (
                <li>
                  <button
                    type="button"
                    onClick={onConnectZoom}
                    className={LINK_BUTTON}
                    style={{ color: "var(--ed-accent)" }}
                  >
                    Connect Zoom
                  </button>
                </li>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
