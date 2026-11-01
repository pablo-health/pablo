// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UpdateNotice
 *
 * Mounted once in the dashboard shell, for a practice owner or admin. On the
 * single-VM install it says when a newer release is published and offers to
 * install it; everywhere else the API answers 404 and this renders nothing.
 *
 * The button only asks. The host picks the version (the latest published
 * release, whatever the request says), takes a backup first, and goes back to
 * the running release if the new one does not start. This follows its
 * progress and reports what happened, not what was hoped for.
 */

"use client"

import { useState, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { useAuthMutation } from "@/hooks/useAuthQuery"
import { useSoftwareUpdate, useUpdateProgress, type UpdateOutcome } from "@/hooks/useSoftwareUpdate"
import { ApiError } from "@/lib/api/client"
import { requestSoftwareUpdate, type SoftwareUpdate } from "@/lib/api/softwareUpdate"

interface Started {
  target: string
  /** The status file's `at` when the request went in; older results are not this update's. */
  baselineAt: string | null
}

export function UpdateNotice({ isPracticeAdmin }: { isPracticeAdmin: boolean }) {
  const router = useRouter()
  const { data } = useSoftwareUpdate(isPracticeAdmin)
  const [confirming, setConfirming] = useState(false)
  const [started, setStarted] = useState<Started | null>(null)
  const [startFailed, setStartFailed] = useState(false)
  const start = useAuthMutation<{ requested: string }>({ mutationFn: requestSoftwareUpdate })

  // An update already under way when the page loaded (another admin, or a
  // reload mid-update) is followed the same way as one started here.
  const running = data?.status?.state === "updating" ? data.status : null
  const target = started?.target ?? running?.to ?? null
  const outcome = useUpdateProgress(target, started ? started.baselineAt : null)

  if (!data) return null
  if (outcome) return <Notice>{outcomeMessage(outcome, target ?? data.latest, data)}</Notice>
  if (!data.available) return null

  const follow = () => setStarted({ target: data.latest, baselineAt: data.status?.at ?? null })
  const confirm = () => {
    if (start.isPending) return
    setStartFailed(false)
    start.mutate(undefined, {
      onSuccess: follow,
      onError: (err) => {
        if (err instanceof ApiError && err.code === "MFA_REQUIRED") {
          router.push("/mfa-step-up")
        } else if (err instanceof ApiError && err.code === "UPDATE_IN_PROGRESS") {
          follow()
        } else {
          setStartFailed(true)
        }
      },
    })
  }

  const lastTry = data.status
  const lastTryFailed =
    (lastTry?.state === "failed" || lastTry?.state === "rolled_back") && lastTry.to === data.latest

  return (
    <Notice>
      <div className="flex-1 space-y-1">
        <p className="font-medium">A new version of Pablo is available: {data.latest}</p>
        {lastTryFailed && (
          <p>
            The last update to {data.latest} didn&rsquo;t finish, so Pablo is still on{" "}
            {data.current}.
          </p>
        )}
        {confirming && <p>Pablo will be unavailable for about a minute while it updates.</p>}
        {startFailed && <p role="alert">Couldn&rsquo;t start the update. Try again.</p>}
        {data.notes_url.startsWith("https://") && (
          <a
            href={data.notes_url}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-primary-700 underline-offset-2 hover:underline"
          >
            Release notes
          </a>
        )}
      </div>
      <div className="flex shrink-0 gap-2">
        {confirming && (
          <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        )}
        <Button size="sm" onClick={confirming ? confirm : () => setConfirming(true)}>
          {start.isPending ? "Starting…" : "Update now"}
        </Button>
      </div>
    </Notice>
  )
}

function outcomeMessage(outcome: UpdateOutcome, target: string, before: SoftwareUpdate): ReactNode {
  switch (outcome) {
    case "updating":
      return <p className="flex-1">Updating. Pablo will be back in about a minute.</p>
    case "done":
      return (
        <>
          <p className="flex-1">Pablo is now running {target}. Reload the page to use it.</p>
          <Button size="sm" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </>
      )
    case "rolled_back":
      return (
        <p className="flex-1">
          The update to {target} didn&rsquo;t finish, so Pablo stayed on {before.current}.
        </p>
      )
    case "failed":
      return (
        <p className="flex-1">
          The update to {target} didn&rsquo;t finish. The server&rsquo;s log has the details:{" "}
          <code>journalctl -u pablo-update-request</code>
        </p>
      )
    case "timed_out":
      return (
        <p className="flex-1">
          Pablo hasn&rsquo;t confirmed {target} yet. Reload the page in a few minutes to check.
        </p>
      )
  }
}

function Notice({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      data-testid="update-notice"
      className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-primary-200 bg-primary-50 px-4 py-3 text-sm text-primary-800"
    >
      {children}
    </div>
  )
}
