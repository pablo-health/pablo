// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import type { PatientResponse } from "@/types/patients"
import { PatientForm } from "./PatientForm"

interface EditPatientButtonProps {
  patient: PatientResponse
}

export function EditPatientButton({ patient }: EditPatientButtonProps) {
  const { readOnly } = useReadOnlyMode()
  const [open, setOpen] = useState(false)

  if (readOnly) return null

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Pencil className="h-4 w-4" />
        Edit
      </Button>
      <PatientForm mode="edit" patient={patient} open={open} onOpenChange={setOpen} />
    </>
  )
}
