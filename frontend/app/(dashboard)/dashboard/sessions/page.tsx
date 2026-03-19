// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Sessions List Page
 *
 * Displays list of all sessions with ability to upload new sessions.
 */

"use client"

import { SessionsTable } from "@/components/sessions/SessionsTable"
import { UploadTranscriptDialog } from "@/components/sessions/UploadTranscriptDialog"
import { Button } from "@/components/ui/button"
import { Plus } from "lucide-react"

export default function SessionsPage() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-display font-bold text-neutral-900">
            Sessions
          </h1>
          <p className="text-neutral-600 mt-2">
            View and manage therapy sessions
          </p>
        </div>
        <UploadTranscriptDialog
          trigger={
            <Button
              size="lg"
              className="bg-secondary-600 hover:bg-secondary-700 text-white"
            >
              <Plus className="mr-2 h-4 w-4" />
              Upload Session
            </Button>
          }
        />
      </div>

      {/* Sessions Table (handles its own loading/error states) */}
      <SessionsTable />
    </div>
  )
}
