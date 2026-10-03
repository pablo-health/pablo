// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Patients view with two tabs:
 *
 *   1. "All patients" — the existing live patient table.
 *   2. "Recently deleted" — soft-deleted patients still inside the
 *      30-day undo window (THERAPY-yg2). After 30 days the row
 *      disappears from this UI; the day-30 hard-purge cron
 *      (THERAPY-cgy) physically removes it shortly thereafter.
 *
 * This wrapper exists so PatientTable can stay focused on the live
 * listing — its tests (and the existing search/CRUD UX) don't have
 * to know about the recently-deleted slice.
 */

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PatientTable } from "./PatientTable"
import { RecentlyDeletedPatients } from "./RecentlyDeletedPatients"

export function PatientsView() {
  return (
    <Tabs defaultValue="all" className="w-full">
      <TabsList>
        <TabsTrigger value="all">All patients</TabsTrigger>
        <TabsTrigger value="recently-deleted">Recently deleted</TabsTrigger>
      </TabsList>
      <TabsContent value="all">
        <PatientTable />
      </TabsContent>
      <TabsContent value="recently-deleted">
        <RecentlyDeletedPatients />
      </TabsContent>
    </Tabs>
  )
}
