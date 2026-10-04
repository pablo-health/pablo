// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// --- Sessions on the clinician's own calendar that nobody has answered yet ---
//
// When following is on, events on the followed calendar that look like sessions
// come in as open rows. Each asks "who is this?" once per client; an answer
// turns that client's events into appointments that follow their event.
// Titles are the calendar's own wording, shown to the clinician and nowhere
// else.

import type { SeriesMatch, SuggestedName } from "./scheduling"
import type { SessionResponse } from "@/types/sessions"
import { get, post, put } from "./client"

/** One open event on the clinician's calendar, not yet an appointment. */
export interface OutsideSession {
  id: string
  source: string
  source_identifier: string
  title: string
  start_at: string
  end_at: string
}

export interface OutsideSessionList {
  events: OutsideSession[]
}

/** One "who is this?" question: a series, every event with one title, or —
 * when the title can't say which client the next event is (initials, a name
 * two charts share) — one event. */
export interface OutsideQuestion {
  key: string
  source: string
  source_identifier: string
  title: string
  recurring: boolean
  /** Open occurrences the answer will settle. */
  sessions: number
  next_start_at: string
  match: SeriesMatch
  /** The name to fill in if this becomes a new client, when the title
   * plainly gives one. A part it doesn't give whole is empty. */
  suggested_name?: SuggestedName | null
  /** Set when the question is about this one event; handed back with the answer. */
  outside_session_id?: string | null
  /** The suggested client's chart is inactive or on hold; confirming may reactivate it. */
  client_inactive?: boolean
}

export interface OutsideQuestions {
  count: number
  questions: OutsideQuestion[]
}

/** An answer to one question. `patient_id` null with `new_client_name` (the
 * title) adds a new client, named as typed in `new_client_first_name` and
 * `new_client_last_name`; left blank, by the title's name part. */
export interface OutsideAnswer {
  source: string
  source_identifier: string
  patient_id: string | null
  new_client_name: string | null
  new_client_first_name?: string
  new_client_last_name?: string
  not_a_client: boolean
  /** The one event this answers, when the question was about one event. */
  outside_session_id?: string | null
  /** Make an inactive client's chart active again while booking. */
  reactivate?: boolean
}

/** An answered session that wasn't booked: another appointment was there. */
export interface NotAddedSession {
  outside_session_id: string
  client_name: string
  start_at: string
}

export interface OutsideAnswerResult {
  answered: number
  appointments_created: number
  appointments: { outside_session_id: string; appointment_id: string }[]
  not_added: NotAddedSession[]
}

export async function listOutsideSessions(
  start: string,
  end: string
): Promise<OutsideSessionList> {
  return get<OutsideSessionList>(
    `/api/calendar/outside-sessions?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
  )
}

export async function getOutsideQuestions(): Promise<OutsideQuestions> {
  return get<OutsideQuestions>("/api/calendar/outside-sessions/questions")
}

export async function answerOutsideSessions(
  answers: OutsideAnswer[]
): Promise<OutsideAnswerResult> {
  return post<OutsideAnswerResult>("/api/calendar/outside-sessions/answer", { answers })
}

/** A calendar the connection can read, offered to be followed. */
export interface FollowableCalendar {
  id: string
  name: string
  primary: boolean
}

export interface FollowableCalendars {
  /** The main calendar first. */
  calendars: FollowableCalendar[]
  /** The calendar followed now, by the id it has in `calendars`. */
  follow_calendar_id: string | null
}

export async function listFollowableCalendars(): Promise<FollowableCalendars> {
  return get<FollowableCalendars>("/api/google-calendar/calendars")
}

/** Follow a calendar (`"primary"` for the main one), or stop with null. */
export async function setFollowedCalendar(
  calendarId: string | null
): Promise<{ follow_calendar_id: string | null }> {
  return put<{ follow_calendar_id: string | null }>("/api/google-calendar/followed-calendar", {
    calendar_id: calendarId,
  })
}

/** Start the session (and its note) for an appointment. */
export async function startSessionFromAppointment(
  appointmentId: string
): Promise<SessionResponse> {
  return post<SessionResponse>(`/api/appointments/${appointmentId}/start-session`, {})
}
