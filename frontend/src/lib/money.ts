// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Converting between stored money and the money a person types or reads.
 *
 * The browser counterpart of `app.money` — same rule, same reason: money
 * crosses the API as integer minor units (cents), and the conversion to and
 * from what a human sees happens once, here, rather than on every screen that
 * shows a fee. Getting it backwards is a hundred-fold error that does not
 * announce itself: the figure still looks plausible, and surfaces later as a
 * charge for $1.60 or for $16,000.
 *
 * CURRENCY, deliberately: this module assumes 100 minor units to the major
 * unit, exactly as the backend does. That is wrong outside the dollar — the
 * yen has no minor unit at all — and going multi-currency means turning
 * `MINOR_UNITS` into a per-currency exponent in both places. What matters is
 * that there is a "both places" rather than thirty.
 */

/** Minor units per major unit. See the note about currency above. */
const MINOR_UNITS = 100

const formatters = new Map<string, Intl.NumberFormat>()

function formatterFor(currency: string): Intl.NumberFormat {
  const code = currency.toUpperCase()
  let formatter = formatters.get(code)
  if (!formatter) {
    formatter = new Intl.NumberFormat(undefined, { style: "currency", currency: code })
    formatters.set(code, formatter)
  }
  return formatter
}

/**
 * Render stored cents as money.
 *
 * Dividing by 100 is safe on the way *out* in a way it is not on the way in:
 * the input is already an exact integer count of cents, and the formatter
 * rounds to the currency's own precision. It is the multiplication in
 * `dollarsToCents` that has to avoid floating point.
 */
export function formatCents(cents: number, currency = "usd"): string {
  return formatterFor(currency).format(cents / MINOR_UNITS)
}

/**
 * Convert a dollar amount a clinician typed into stored cents.
 *
 * Returns `null` for anything that is not a positive amount, so an empty or
 * half-typed field leaves the action disabled rather than charging zero.
 *
 * Parsed as text, never as a float. `160.10 * 100` is `16009.999…` in binary
 * floating point and truncates to 16009 — a penny lost, silently, on a value
 * the user typed exactly. Splitting the string on the decimal point cannot
 * lose it.
 */
export function dollarsToCents(input: string): number | null {
  const trimmed = input.trim().replace(/^\$/, "").replace(/,/g, "")
  if (!/^\d*(\.\d{0,2})?$/.test(trimmed) || trimmed === "" || trimmed === ".") return null

  const [whole, fraction = ""] = trimmed.split(".")
  const cents = Number(whole || "0") * MINOR_UNITS + Number(fraction.padEnd(2, "0"))
  return cents > 0 ? cents : null
}
