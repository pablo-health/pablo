// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText, Folder } from "lucide-react"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { usePatientNotes } from "@/hooks/useNotes"
import { usePatientDocuments } from "@/hooks/usePatientDocuments"

interface PatientChartTabsProps {
  patientId: string
}

function CountBadge({ count }: { count: number }) {
  if (count === 0) return null
  return (
    <span className="ml-1 rounded-full bg-neutral-200 px-1.5 py-0.5 text-xs font-medium text-neutral-700">
      {count}
    </span>
  )
}

export function PatientChartTabs({ patientId }: PatientChartTabsProps) {
  const { data: notes } = usePatientNotes(patientId)
  const { data: documents } = usePatientDocuments(patientId)

  const noteCount = notes?.total ?? 0
  const documentCount = documents?.total ?? 0

  return (
    <div className="card">
      <h2 className="mb-4 text-xl font-display font-bold text-neutral-900">
        Chart
      </h2>
      <Tabs defaultValue="notes">
        <TabsList>
          <TabsTrigger value="notes">
            <FileText className="h-4 w-4" />
            Notes
            <CountBadge count={noteCount} />
          </TabsTrigger>
          <TabsTrigger value="documents">
            <Folder className="h-4 w-4" />
            Documents
            <CountBadge count={documentCount} />
          </TabsTrigger>
        </TabsList>
        <TabsContent value="notes" className="pt-4">
          <p className="text-sm text-neutral-500">
            {noteCount > 0
              ? `${noteCount} note${noteCount === 1 ? "" : "s"} on file.`
              : "No notes yet."}
          </p>
        </TabsContent>
        <TabsContent value="documents" className="pt-4">
          <p className="text-sm text-neutral-500">
            {documentCount > 0
              ? `${documentCount} document${documentCount === 1 ? "" : "s"} on file.`
              : "No documents yet."}
          </p>
        </TabsContent>
      </Tabs>
    </div>
  )
}
