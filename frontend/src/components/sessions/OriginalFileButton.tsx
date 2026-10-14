// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Opens the file an imported note was read from, kept in the client's
 * documents. The signed URL is fetched after the click, so the tab is opened
 * first, inside the click, the way the Documents tab does it: a popup blocker
 * would stop one opened after the await.
 */

"use client"

import { useState } from "react"
import { ExternalLink } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getPatientDocumentDownloadUrl } from "@/lib/api/patientDocuments"

export function OriginalFileButton({ documentId }: { documentId: string }) {
  const [failed, setFailed] = useState(false)

  const open = async () => {
    setFailed(false)
    const tab = window.open("about:blank", "_blank")
    if (tab) tab.opener = null
    try {
      const url = await getPatientDocumentDownloadUrl(documentId, undefined, "inline")
      if (tab) tab.location.href = url
      else window.location.assign(url)
    } catch {
      tab?.close()
      setFailed(true)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button type="button" variant="outline" size="sm" onClick={open}>
        <ExternalLink className="mr-1.5 h-4 w-4" aria-hidden />
        Open original file
      </Button>
      {failed && (
        <span role="alert" className="text-sm text-red-600">
          The file didn&apos;t open. Try again.
        </span>
      )}
    </div>
  )
}
