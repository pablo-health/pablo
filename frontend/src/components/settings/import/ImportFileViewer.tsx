// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * "View" for one record in the import review: opens the source file from
 * the uploaded archive inline, so a therapist deciding whose record this is
 * can read it. The file is fetched when opened and released when closed.
 */

import { useEffect, useRef, useState } from "react"
import { fetchImportFile } from "@/lib/api/migration"

interface Props {
  runId: string
  path: string
  label: string
}

export function ImportFileViewer({ runId, path, label }: Props) {
  const [url, setUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const current = useRef<string | null>(null)

  // Release the object URL when the viewer goes away.
  useEffect(
    () => () => {
      if (current.current) URL.revokeObjectURL(current.current)
    },
    [],
  )

  async function open() {
    setLoading(true)
    setFailed(false)
    try {
      const blob = await fetchImportFile(runId, path)
      const next = URL.createObjectURL(blob)
      current.current = next
      setUrl(next)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }

  function close() {
    if (current.current) URL.revokeObjectURL(current.current)
    current.current = null
    setUrl(null)
    setFailed(false)
  }

  const shown = url !== null || failed

  return (
    <div>
      <button
        type="button"
        onClick={() => (shown ? close() : void open())}
        disabled={loading}
        className="text-xs font-medium underline underline-offset-2"
        aria-expanded={shown}
      >
        {shown ? "Hide" : "View"}
      </button>
      {failed && (
        <p role="alert" className="mt-2 text-xs text-destructive">
          This file isn&apos;t available any more.
        </p>
      )}
      {url && (
        <iframe
          title={`Source file: ${label}`}
          src={url}
          data-testid="import-file-viewer"
          className="mt-2 h-96 w-full rounded border border-border"
        />
      )}
    </div>
  )
}
