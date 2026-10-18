// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, type ReactNode } from "react"
import Link from "next/link"
import {
  Activity,
  ClipboardList,
  CreditCard,
  FileText,
  History as HistoryIcon,
  ListChecks,
  Folder,
  Pill,
  Stethoscope,
} from "lucide-react"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { ChartUpdatesWaiting } from "@/components/notes/chartUpdates/ChartUpdatesWaiting"
import { Skeleton } from "@/components/ui/skeleton"
import { NewNoteButton } from "@/components/notes/NewNoteButton"
import { OnlyYouBadge } from "@/components/notes/OnlyYouBadge"
import { PatientDocuments } from "@/components/patients/PatientDocuments"
import { OutcomeMeasuresTab } from "@/components/outcomeMeasures/OutcomeMeasuresTab"
import { DiagnosesTab } from "@/components/diagnoses/DiagnosesTab"
import { ProblemListTab } from "@/components/problems/ProblemListTab"
import { ChartHistoryTab } from "@/components/chartHistory/ChartHistoryTab"
import { MedicationsTab } from "@/components/medications/MedicationsTab"
import { PaymentsTab } from "@/components/payments/PaymentsTab"
import { BalanceTab } from "@/components/payments/BalanceTab"
import { InsuranceCard } from "@/components/insurance/InsuranceCard"
import { IntakeCard } from "@/components/patients/IntakeCard"
import { usePatientNotes } from "@/hooks/useNotes"
import { useNoteTypeLabel } from "@/hooks/useNoteTypes"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { usePatientDocuments } from "@/hooks/usePatientDocuments"
import { usePatientOutcomeMeasures } from "@/hooks/useOutcomeMeasures"
import { usePatientDiagnoses } from "@/hooks/useDiagnoses"
import { usePatientProblems } from "@/hooks/useProblems"
import { usePatientMedications } from "@/hooks/useMedications"
import { usePatientCharges } from "@/hooks/usePayments"
import {
  formatNoteDateTime,
  noteHref,
  noteListDate,
  noteOrigin,
  noteStatus,
} from "@/lib/noteDisplay"

const PREVIEW_LIMIT = 3

interface PatientChartTabsProps {
  patientId: string
  /** Which tab to open on; anything that is not a tab here opens Notes. */
  initialTab?: string
}

const TABS = [
  "notes",
  "intake",
  "documents",
  "measures",
  "problems",
  "history",
  "diagnoses",
  "medications",
  "billing",
] as const

/**
 * Balance, Payments and Insurance were tabs of their own before they became
 * sections of Billing. A link that still names one opens Billing and scrolls
 * to that section.
 */
const BILLING_SECTIONS = ["balance", "payments", "insurance"] as const
type BillingSectionKey = (typeof BILLING_SECTIONS)[number]

const billingSectionId = (section: BillingSectionKey) => `billing-${section}`

function BillingSection({
  section,
  title,
  children,
}: {
  section: BillingSectionKey
  title: string
  children: ReactNode
}) {
  const id = billingSectionId(section)
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="scroll-mt-4">
      <h3 id={`${id}-heading`} className="mb-3 text-base font-semibold text-neutral-900">
        {title}
      </h3>
      {children}
    </section>
  )
}

function CountBadge({ count }: { count: number }) {
  if (count === 0) return null
  return (
    <span className="ml-1 rounded-full bg-neutral-200 px-1.5 py-0.5 text-xs font-medium text-neutral-700">
      {count}
    </span>
  )
}

function NotesTab({ patientId }: { patientId: string }) {
  const { data, isPending, error } = usePatientNotes(patientId)
  const noteTypeLabel = useNoteTypeLabel()
  const people = usePeopleTerm()

  // isPending, not isLoading: the query is disabled until sign-in resolves,
  // and a disabled query is not "loading" yet has no data. Reading that as
  // "no notes" showed the empty state early, then swapped it for the
  // skeleton once the fetch began — unmounting its New note button and
  // closing a dialog the clinician had just opened.
  if (isPending) {
    return (
      <div className="space-y-2">
        {Array.from({ length: PREVIEW_LIMIT }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load notes."}
      </p>
    )
  }

  if (!data || data.total === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-8 text-center">
        <FileText className="h-8 w-8 text-neutral-300" />
        <p className="text-sm text-neutral-600">No notes yet for this {people.one}.</p>
        <NewNoteButton patientId={patientId} />
      </div>
    )
  }

  const recent = data.data.slice(0, PREVIEW_LIMIT)

  return (
    <div className="space-y-3">
      <ul className="space-y-2">
        {recent.map((note) => {
          const status = noteStatus(note)
          return (
            <li key={note.id}>
              <Link
                href={noteHref(patientId, note)}
                className="flex items-center justify-between gap-3 rounded-lg border border-neutral-100 px-3 py-2.5 transition-colors hover:border-primary-200 hover:bg-primary-50/40"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="inline-flex items-center rounded bg-neutral-100 px-2 py-0.5 text-xs font-medium capitalize text-neutral-700">
                    {noteTypeLabel(note.note_type)}
                  </span>
                  {note.restricted && <OnlyYouBadge />}
                  <span className="text-xs text-neutral-500">{noteOrigin(note)}</span>
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="text-xs text-neutral-500">
                    {formatNoteDateTime(noteListDate(note))}
                  </span>
                  <span
                    className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${status.className}`}
                  >
                    {status.label}
                  </span>
                </span>
              </Link>
            </li>
          )
        })}
      </ul>
      <div className="flex items-center justify-between pt-1">
        <Link
          href={`/dashboard/patients/${patientId}/notes`}
          className="inline-flex items-center gap-1 text-sm text-primary-700 transition-colors hover:text-primary-900"
        >
          <FileText className="h-4 w-4" />
          View all notes
        </Link>
        <NewNoteButton patientId={patientId} />
      </div>
    </div>
  )
}

export function PatientChartTabs({ patientId, initialTab }: PatientChartTabsProps) {
  const openSection = BILLING_SECTIONS.find((section) => section === initialTab)
  const openOn = openSection ? "billing" : (TABS.find((tab) => tab === initialTab) ?? "notes")
  const { data: notes } = usePatientNotes(patientId)
  const { data: documents } = usePatientDocuments(patientId)
  const { data: measures } = usePatientOutcomeMeasures(patientId)
  const { data: problems } = usePatientProblems(patientId)
  const { data: diagnoses } = usePatientDiagnoses(patientId)
  const { data: medications } = usePatientMedications(patientId)
  const { data: charges } = usePatientCharges(patientId)

  const noteCount = notes?.total ?? 0
  const documentCount = documents?.total ?? 0
  const measureCount = measures?.total ?? 0
  const problemCount = problems?.data.filter((p) => p.status === "active").length ?? 0
  const diagnosisCount = diagnoses?.total ?? 0
  const medicationCount =
    medications?.data.filter((m) => m.status === "active").length ?? 0
  const chargeCount = charges?.length ?? 0

  useEffect(() => {
    if (!openSection) return
    document.getElementById(billingSectionId(openSection))?.scrollIntoView?.({ block: "start" })
  }, [openSection])

  return (
    <div className="card">
      <h2 className="mb-4 text-xl font-display font-bold text-neutral-900">
        Chart
      </h2>
      {/* What the client's notes and intake forms would change on the chart, decided here
          before the next visit; nothing when nothing is waiting. */}
      <ChartUpdatesWaiting patientId={patientId} />
      <Tabs defaultValue={openOn}>
        <TabsList>
          <TabsTrigger value="notes">
            <FileText className="h-4 w-4" />
            Notes
            <CountBadge count={noteCount} />
          </TabsTrigger>
          <TabsTrigger value="intake">
            <ClipboardList className="h-4 w-4" />
            Intake
          </TabsTrigger>
          <TabsTrigger value="documents">
            <Folder className="h-4 w-4" />
            Documents
            <CountBadge count={documentCount} />
          </TabsTrigger>
          <TabsTrigger value="measures">
            <Activity className="h-4 w-4" />
            Measures
            <CountBadge count={measureCount} />
          </TabsTrigger>
          <TabsTrigger value="problems">
            <ListChecks className="h-4 w-4" />
            Problems
            <CountBadge count={problemCount} />
          </TabsTrigger>
          <TabsTrigger value="history">
            <HistoryIcon className="h-4 w-4" />
            History
          </TabsTrigger>
          <TabsTrigger value="diagnoses">
            <Stethoscope className="h-4 w-4" />
            Diagnoses
            <CountBadge count={diagnosisCount} />
          </TabsTrigger>
          <TabsTrigger value="medications">
            <Pill className="h-4 w-4" />
            Medications
            <CountBadge count={medicationCount} />
          </TabsTrigger>
          <TabsTrigger value="billing">
            <CreditCard className="h-4 w-4" />
            Billing
            <CountBadge count={chargeCount} />
          </TabsTrigger>
        </TabsList>
        <TabsContent value="notes" className="pt-4">
          <NotesTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="intake" className="pt-4">
          <IntakeCard patientId={patientId} />
        </TabsContent>
        <TabsContent value="documents" className="pt-4">
          <PatientDocuments patientId={patientId} />
        </TabsContent>
        <TabsContent value="measures" className="pt-4">
          <OutcomeMeasuresTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="problems" className="pt-4">
          <ProblemListTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="history" className="pt-4">
          <ChartHistoryTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="diagnoses" className="pt-4">
          <DiagnosesTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="medications" className="pt-4">
          <MedicationsTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="billing" className="space-y-8 pt-4">
          <BillingSection section="balance" title="Balance">
            <BalanceTab patientId={patientId} />
          </BillingSection>
          <BillingSection section="payments" title="Payments">
            <PaymentsTab patientId={patientId} />
          </BillingSection>
          <BillingSection section="insurance" title="Insurance">
            <InsuranceCard patientId={patientId} />
          </BillingSection>
        </TabsContent>
      </Tabs>
    </div>
  )
}
