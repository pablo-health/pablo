// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's schedule as the week and day views draw it: the working
 * hours it set, less the time it blocked off — the same picture as the
 * Settings hours grid.
 *
 * This is deliberately not "can a session start here". That answer (the
 * engine's free slots) also depends on session length, buffers, day caps and
 * what is already booked. Drawn on the calendar it ended a 9-to-5 day
 * somewhere between 4:15 and 4:40, put a band in front of every session, and
 * read as hours that had not been saved. The booking surfaces — the slot
 * picker in the appointment sheet and the public booking pages — still ask
 * the engine, which stays the only authority on whether a time can be booked.
 *
 * What counts as the schedule, mirroring the engine's own reading of the
 * rules (backend/app/scheduling_engine/services/availability.py):
 * - practice-wide `working_hours` for the weekday are the working time;
 * - a whole-day block (`matchWholeDayBlockRule`) empties the day;
 * - practice-wide `block_time_range` rules cut their range out of every day.
 * Rules scoped to one appointment type only narrow what that type is offered
 * within these hours, so they are left to the booking surfaces.
 *
 * Rules are kept in the practice's own zone and the grid is drawn in the
 * browser's, so each range goes through a real instant: a clinician looking
 * from another zone sees the hours where they actually fall.
 */

import { addDays, endOfWeek, isSameDay, startOfDay } from "date-fns"
import type { AvailabilityRule } from "@/types/availability"
import { format } from "./dateUtils"
import { jsWeekdayToRuleDay, matchWholeDayBlockRule } from "./unavailability"

export interface MinuteRange {
  startMin: number
  endMin: number
}

const MINUTES_PER_DAY = 24 * 60
const MS_PER_MINUTE = 60_000

/** How far ahead to look for the next working day before giving up. */
const NEXT_WORKING_DAY_HORIZON = 14

function clockToMinutes(value: unknown): number | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(String(value ?? ""))
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

/** A calendar date as a local Date at midnight, from "yyyy-MM-dd". */
function localDate(dateStr: string): Date {
  const [y, m, d] = dateStr.split("-").map(Number)
  return new Date(y, m - 1, d)
}

function isPracticeWide(rule: AvailabilityRule): boolean {
  return !rule.appointment_type_id
}

function subtract(ranges: MinuteRange[], cut: MinuteRange): MinuteRange[] {
  return ranges.flatMap((range) => {
    if (cut.endMin <= range.startMin || cut.startMin >= range.endMin) return [range]
    const pieces: MinuteRange[] = []
    if (cut.startMin > range.startMin) pieces.push({ startMin: range.startMin, endMin: cut.startMin })
    if (cut.endMin < range.endMin) pieces.push({ startMin: cut.endMin, endMin: range.endMin })
    return pieces
  })
}

/**
 * The working time on one of the practice's own calendar dates, in minutes
 * since the practice's midnight.
 */
export function practiceDayRanges(rules: AvailabilityRule[], dateStr: string): MinuteRange[] {
  const date = localDate(dateStr)
  if (matchWholeDayBlockRule(rules, date)) return []
  const ruleDay = jsWeekdayToRuleDay(date.getDay())
  let ranges: MinuteRange[] = []
  for (const rule of rules) {
    if (rule.rule_type !== "working_hours" || !isPracticeWide(rule)) continue
    if (Number(rule.params.day_of_week) !== ruleDay) continue
    const startMin = clockToMinutes(rule.params.start)
    const endMin = clockToMinutes(rule.params.end)
    if (startMin === null || endMin === null || endMin <= startMin) continue
    ranges.push({ startMin, endMin })
  }
  for (const rule of rules) {
    if (rule.rule_type !== "block_time_range" || !isPracticeWide(rule)) continue
    const startMin = clockToMinutes(rule.params.start)
    const endMin = clockToMinutes(rule.params.end)
    if (startMin === null || endMin === null) continue
    ranges = subtract(ranges, { startMin, endMin })
  }
  return ranges.sort((a, b) => a.startMin - b.startMin)
}

/** The zone's offset from UTC at an instant, in ms (positive east of UTC). */
function zoneOffsetMs(instantMs: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
  }).formatToParts(new Date(instantMs))
  const field = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  const asUtc = Date.UTC(
    field("year"),
    field("month") - 1,
    field("day"),
    field("hour"),
    field("minute"),
    field("second"),
  )
  return asUtc - Math.floor(instantMs / 1000) * 1000
}

/** The instant at which the wall clock in `timeZone` reads `minute` on `dateStr`. */
export function zonedInstant(dateStr: string, minute: number, timeZone: string): number {
  const [y, m, d] = dateStr.split("-").map(Number)
  const wall = Date.UTC(y, m - 1, d) + minute * MS_PER_MINUTE
  // Once from the wall time read as UTC, then again from the first guess, so
  // a range that sits across a daylight-saving change lands on the right side.
  const first = wall - zoneOffsetMs(wall, timeZone)
  return wall - zoneOffsetMs(first, timeZone)
}

function localMinutes(instantMs: number, day: Date): number {
  const nextMidnight = addDays(day, 1).getTime()
  if (instantMs >= nextMidnight) return MINUTES_PER_DAY
  if (instantMs <= day.getTime()) return 0
  const at = new Date(instantMs)
  return at.getHours() * 60 + at.getMinutes()
}

function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}

/**
 * The working time that falls on one column of the grid — a day in the
 * browser's zone — in minutes since the browser's midnight. The practice's
 * dates either side are included, because with the zones apart one of them
 * can reach into this day.
 */
export function workingRangesForDay(
  rules: AvailabilityRule[],
  day: Date,
  timeZone: string = browserTimeZone(),
): MinuteRange[] {
  const dayStart = startOfDay(day)
  const dayEnd = addDays(dayStart, 1).getTime()
  const ranges: MinuteRange[] = []
  for (const offset of [-1, 0, 1]) {
    const dateStr = format(addDays(dayStart, offset), "yyyy-MM-dd")
    for (const range of practiceDayRanges(rules, dateStr)) {
      const start = zonedInstant(dateStr, range.startMin, timeZone)
      const end = zonedInstant(dateStr, range.endMin, timeZone)
      if (end <= dayStart.getTime() || start >= dayEnd) continue
      const startMin = localMinutes(start, dayStart)
      const endMin = localMinutes(end, dayStart)
      if (endMin > startMin) ranges.push({ startMin, endMin })
    }
  }
  return ranges.sort((a, b) => a.startMin - b.startMin)
}

/**
 * The bands to shade as outside working hours within [dayStartHour,
 * dayEndHour): the complement of `open`, in minutes since midnight.
 */
export function offHoursGaps(
  open: MinuteRange[],
  dayStartHour: number,
  dayEndHour: number,
): MinuteRange[] {
  const windowStart = dayStartHour * 60
  const windowEnd = dayEndHour * 60
  const clamped = open
    .map((r) => ({
      startMin: Math.min(Math.max(r.startMin, windowStart), windowEnd),
      endMin: Math.min(Math.max(r.endMin, windowStart), windowEnd),
    }))
    .filter((r) => r.endMin > r.startMin)
    .sort((a, b) => a.startMin - b.startMin)

  const gaps: MinuteRange[] = []
  let cursor = windowStart
  for (const r of clamped) {
    if (r.startMin > cursor) gaps.push({ startMin: cursor, endMin: r.startMin })
    cursor = Math.max(cursor, r.endMin)
  }
  if (cursor < windowEnd) gaps.push({ startMin: cursor, endMin: windowEnd })
  return gaps
}

/**
 * How far down a day's column is already past, in minutes since midnight:
 * the whole day before today, up to now today, nothing after.
 */
export function pastUntilMinute(day: Date, now: Date): number {
  const dayStart = startOfDay(day)
  if (isSameDay(dayStart, now)) return now.getHours() * 60 + now.getMinutes()
  return dayStart < now ? MINUTES_PER_DAY : 0
}

/** The first day from `now` on with working time still ahead of it. */
export function nextWorkingDay(
  rules: AvailabilityRule[],
  now: Date,
  timeZone?: string,
): Date | null {
  for (let offset = 0; offset < NEXT_WORKING_DAY_HORIZON; offset++) {
    const day = addDays(startOfDay(now), offset)
    const after = offset === 0 ? pastUntilMinute(day, now) : 0
    if (workingRangesForDay(rules, day, timeZone).some((r) => r.endMin > after)) return day
  }
  return null
}

/**
 * Where a calendar opened right after setup should land. The current week,
 * unless nothing is left in it — no working time ahead and no session still
 * to come — in which case the week of the next working day. Finishing setup
 * on a Friday evening with Fridays off would otherwise open on a week that is
 * all behind the clinician.
 */
export function openingAnchor(
  rules: AvailabilityRule[],
  appointments: { start_at: string; end_at: string }[],
  now: Date,
  timeZone?: string,
): Date {
  const weekEnd = endOfWeek(now, { weekStartsOn: 0 })
  const sessionAhead = appointments.some((a) => {
    const end = new Date(a.end_at)
    return end > now && new Date(a.start_at) <= weekEnd
  })
  if (sessionAhead) return now
  const next = nextWorkingDay(rules, now, timeZone)
  return next && next > weekEnd ? next : now
}
