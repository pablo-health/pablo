// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * What she sees between asking for credentialing and the first application.
 *
 * Asking is a tick in the billing setup wizard; filing the first application
 * is a person doing it, and that person may be days behind her. In between,
 * `PanelApplications` has nothing to render — correctly, since a clinician
 * who has not started should not be told she has no applications — so the
 * screen showed her an authorisation card and an NPI lookup she had already
 * done, and nothing at all about the thing she asked for.
 *
 * For a process whose defining failure is silence, the first silence was ours.
 *
 * WHAT THIS DOES NOT SAY, each one deliberate:
 *
 * * **No date, and no range.** Panels run 45-180 days on the payer's clock and
 *   nothing we do shortens it. A number here would be the one promise we
 *   cannot keep, and she would measure us against it from the day she read it.
 * * **Not "we'll keep you updated".** Nothing dispatches an update yet. It
 *   becomes true when owner-aware reminders land, and until then it is a
 *   sentence that quietly makes the screen a liar.
 * * **No progress bar, no percentage, no stage.** We do not know which stage
 *   her file is at until an operator files it, and inventing one would be
 *   claiming a readiness nothing has checked.
 *
 * What it does say is the one thing she cannot see for herself: the request
 * reached a person, and where the answer will appear when there is one.
 */
export function RequestReceived() {
  return (
    <section
      className="rounded-xl border border-border bg-card p-5 space-y-3"
      data-testid="credentialing-request-received"
    >
      <h3 className="font-display text-lg font-semibold text-neutral-900">
        Your credentialing request is with us
      </h3>

      <p className="text-sm text-neutral-700">
        You asked us to get you on insurance panels, and that request has
        reached the people who run them. Nothing needs doing from you yet.
      </p>

      {/* Where to look, rather than a promise to tell her. The tracker is
          directly below this card once there is anything in it, so this is a
          description of the screen she is already on and stays true without
          anything having to fire. */}
      <p className="text-sm text-neutral-700">
        As each application goes in, it appears here &mdash; one line per
        insurer, saying where it stands and whose move it is. When an insurer
        asks for something only you can give, that shows up here too, at the
        top.
      </p>
    </section>
  )
}
