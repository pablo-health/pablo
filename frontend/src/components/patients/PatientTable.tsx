// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Pencil, Trash2, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePatientList, useDeletePatient } from "@/hooks/usePatients"
import type { PatientResponse } from "@/types/patients"
import { PatientForm } from "./PatientForm"

function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay)
    return () => clearTimeout(handler)
  }, [value, delay])

  return debouncedValue
}

export function PatientTable() {
  const router = useRouter()
  const [searchTerm, setSearchTerm] = useState("")
  const [formDialogOpen, setFormDialogOpen] = useState(false)
  const [formMode, setFormMode] = useState<"create" | "edit">("create")
  const [selectedPatient, setSelectedPatient] = useState<PatientResponse | undefined>()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [patientToDelete, setPatientToDelete] = useState<PatientResponse | null>(null)
  const [retentionAcknowledged, setRetentionAcknowledged] = useState(false)

  // Debounce search term
  const debouncedSearch = useDebounce(searchTerm, 500)

  // Fetch patients with search
  const { data: patientsResponse, isLoading, error } = usePatientList({
    search: debouncedSearch || undefined,
  })

  const patients = patientsResponse?.data || []

  const deletePatient = useDeletePatient()

  const handleAddPatient = () => {
    setFormMode("create")
    setSelectedPatient(undefined)
    setFormDialogOpen(true)
  }

  const handleEditPatient = (patient: PatientResponse) => {
    setFormMode("edit")
    setSelectedPatient(patient)
    setFormDialogOpen(true)
  }

  const handleDeleteClick = (patient: PatientResponse) => {
    setPatientToDelete(patient)
    setRetentionAcknowledged(false)
    setDeleteDialogOpen(true)
  }

  const handleDeleteDialogChange = (open: boolean) => {
    setDeleteDialogOpen(open)
    if (!open) {
      setRetentionAcknowledged(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (patientToDelete && retentionAcknowledged) {
      try {
        await deletePatient.mutateAsync({
          patientId: patientToDelete.id,
          acknowledgedRetentionObligation: true,
        })
        setDeleteDialogOpen(false)
        setPatientToDelete(null)
        setRetentionAcknowledged(false)
      } catch (error) {
        console.error("Patient delete failed")
      }
    }
  }

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "N/A"
    try {
      return new Date(dateString).toLocaleDateString()
    } catch {
      return "N/A"
    }
  }

  const getStatusBadge = (status: string) => {
    const styles = {
      active: "bg-secondary-100 text-secondary-700",
      inactive: "bg-neutral-100 text-neutral-700",
      on_hold: "bg-yellow-100 text-yellow-700",
    }
    const style = styles[status as keyof typeof styles] || styles.inactive

    return (
      <span className={`inline-flex px-2 py-1 text-xs font-medium rounded-full ${style}`}>
        {status.replace("_", " ")}
      </span>
    )
  }

  return (
    <>
      <div className="space-y-4">
        {/* Search and Add Patient */}
        <div className="flex justify-between items-center gap-4">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-neutral-400 w-4 h-4" />
            <Input
              type="text"
              placeholder="Search patients by name..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10"
            />
          </div>
          <Button onClick={handleAddPatient} className="btn-primary">
            Add Patient
          </Button>
        </div>

        {/* Table */}
        <div className="card">
          {error ? (
            <div className="text-center py-12">
              <p className="text-red-500">Failed to load patients. Please try again.</p>
            </div>
          ) : isLoading ? (
            <div className="text-center py-12">
              <p className="text-neutral-500">Loading patients...</p>
            </div>
          ) : patients.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-neutral-500">
                {searchTerm
                  ? "No patients found matching your search."
                  : "No patients yet. Click \"Add Patient\" to get started."}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Email</TableHead>
                    <TableHead>Phone</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Sessions</TableHead>
                    <TableHead>Next Session</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {patients.map((patient) => (
                    <TableRow
                      key={patient.id}
                      className="cursor-pointer hover:bg-neutral-50"
                      onClick={() => router.push(`/dashboard/patients/${patient.id}`)}
                    >
                      <TableCell className="font-medium">
                        {patient.first_name} {patient.last_name}
                      </TableCell>
                      <TableCell>{patient.email || "N/A"}</TableCell>
                      <TableCell>{patient.phone || "N/A"}</TableCell>
                      <TableCell>{getStatusBadge(patient.status)}</TableCell>
                      <TableCell>{patient.session_count}</TableCell>
                      <TableCell>{formatDate(patient.next_session_date)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2" onClick={(e) => e.stopPropagation()}>
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Edit patient ${patient.first_name} ${patient.last_name}`}
                            onClick={() => handleEditPatient(patient)}
                          >
                            <Pencil className="w-4 h-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Delete patient ${patient.first_name} ${patient.last_name}`}
                            onClick={() => handleDeleteClick(patient)}
                          >
                            <Trash2 className="w-4 h-4 text-red-500" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </div>

      {/* Patient Form Dialog */}
      <PatientForm
        mode={formMode}
        patient={selectedPatient}
        open={formDialogOpen}
        onOpenChange={setFormDialogOpen}
      />

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={handleDeleteDialogChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Patient</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete{" "}
              <strong>
                {patientToDelete?.first_name} {patientToDelete?.last_name}
              </strong>
              ? This action cannot be undone and will also delete all associated sessions.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-start gap-3 rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <Checkbox
              id="retention-acknowledged"
              checked={retentionAcknowledged}
              onCheckedChange={(checked) =>
                setRetentionAcknowledged(checked === true)
              }
              disabled={deletePatient.isPending}
              className="mt-0.5"
            />
            <Label
              htmlFor="retention-acknowledged"
              className="text-sm font-normal leading-relaxed cursor-pointer"
            >
              I confirm I have met my professional retention obligations for
              this patient&apos;s record.
            </Label>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => handleDeleteDialogChange(false)}
              disabled={deletePatient.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deletePatient.isPending || !retentionAcknowledged}
            >
              {deletePatient.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
