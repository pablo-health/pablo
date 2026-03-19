// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ExportReviewCard Component
 *
 * Display session queued for export with tabs for redacted content review
 * and action buttons for approve/skip/flag.
 */

"use client"

import { CheckCircle, SkipForward, Flag } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useExportAction } from "@/hooks/useExportQueue"
import type { ExportQueueItem } from "@/types/sessions"
import { cn } from "@/lib/utils"

export interface ExportReviewCardProps {
  session: ExportQueueItem
}

function formatDate(dateString: string): string {
  const date = new Date(dateString)
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

function SOAPSection({
  title,
  content,
}: {
  title: string
  content: string
}) {
  return (
    <div className="mb-4">
      <h4 className="font-semibold text-sm text-neutral-700 mb-1">{title}</h4>
      <p className="text-sm text-neutral-900 whitespace-pre-wrap">{content}</p>
    </div>
  )
}

export function ExportReviewCard({ session }: ExportReviewCardProps) {
  const actionMutation = useExportAction()
  const [selectedTab, setSelectedTab] = useState("transcript")

  const handleApprove = async () => {
    await actionMutation.mutateAsync({
      sessionId: session.id,
      data: { action: "approve" },
    })
  }

  const handleSkip = async () => {
    await actionMutation.mutateAsync({
      sessionId: session.id,
      data: { action: "skip" },
    })
  }

  const handleFlag = async () => {
    await actionMutation.mutateAsync({
      sessionId: session.id,
      data: { action: "flag", reason: "PII redaction concern" },
    })
  }

  const isPending = actionMutation.isPending

  return (
    <div className="border rounded-lg p-6 bg-white shadow-sm">
      {/* Session metadata */}
      <div className="mb-6">
        <div className="flex items-start justify-between mb-2">
          <div>
            <h3 className="text-lg font-semibold text-neutral-900">
              {session.patient_name}
            </h3>
            <p className="text-sm text-neutral-600">
              Session #{session.session_number} • {formatDate(session.session_date)}
            </p>
          </div>
          {session.quality_rating && (
            <div className="flex items-center gap-1 px-3 py-1 bg-amber-100 text-amber-700 rounded-full text-sm font-medium">
              ⭐ {session.quality_rating}/5
            </div>
          )}
        </div>
        {session.export_queued_at && (
          <p className="text-xs text-neutral-500">
            Queued: {formatDate(session.export_queued_at)}
          </p>
        )}
      </div>

      {/* Tabs for content review */}
      <Tabs value={selectedTab} onValueChange={setSelectedTab} className="mb-6">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="transcript">Redacted Transcript</TabsTrigger>
          <TabsTrigger value="soap">Redacted SOAP Note</TabsTrigger>
        </TabsList>

        <TabsContent value="transcript" className="mt-4">
          {session.redacted_transcript ? (
            <div className="max-h-96 overflow-y-auto border rounded-md p-4 bg-neutral-50">
              <pre className="text-sm text-neutral-900 whitespace-pre-wrap font-mono">
                {session.redacted_transcript}
              </pre>
            </div>
          ) : (
            <div className="border rounded-md p-4 bg-neutral-50 text-center text-neutral-500">
              No redacted transcript available
            </div>
          )}
        </TabsContent>

        <TabsContent value="soap" className="mt-4">
          {session.redacted_soap_note ? (
            <div className="max-h-96 overflow-y-auto border rounded-md p-4 bg-neutral-50">
              <SOAPSection
                title="SUBJECTIVE"
                content={session.redacted_soap_note.subjective}
              />
              <SOAPSection
                title="OBJECTIVE"
                content={session.redacted_soap_note.objective}
              />
              <SOAPSection
                title="ASSESSMENT"
                content={session.redacted_soap_note.assessment}
              />
              <SOAPSection title="PLAN" content={session.redacted_soap_note.plan} />
            </div>
          ) : (
            <div className="border rounded-md p-4 bg-neutral-50 text-center text-neutral-500">
              No redacted SOAP note available
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Action buttons */}
      <div className="flex gap-3">
        <Button
          onClick={handleApprove}
          disabled={isPending}
          className={cn(
            "flex-1",
            "bg-secondary-600 hover:bg-secondary-700",
            "text-white"
          )}
        >
          <CheckCircle className="w-4 h-4 mr-2" />
          Approve for Export
        </Button>

        <Button
          onClick={handleSkip}
          disabled={isPending}
          variant="outline"
          className="flex-1"
        >
          <SkipForward className="w-4 h-4 mr-2" />
          Skip
        </Button>

        <Button
          onClick={handleFlag}
          disabled={isPending}
          variant="outline"
          className={cn(
            "flex-1",
            "border-amber-300 text-amber-700 hover:bg-amber-50"
          )}
        >
          <Flag className="w-4 h-4 mr-2" />
          Flag Issue
        </Button>
      </div>
    </div>
  )
}
