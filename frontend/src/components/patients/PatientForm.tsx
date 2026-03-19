// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Button } from "@/components/ui/button"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useCreatePatient, useUpdatePatient } from "@/hooks/usePatients"
import type { PatientResponse } from "@/types/patients"

const patientFormSchema = z.object({
  first_name: z.string().min(1, "First name is required").max(255),
  last_name: z.string().min(1, "Last name is required").max(255),
  email: z.string().email("Invalid email").optional().or(z.literal("")),
  phone: z.string().min(10, "Phone must be at least 10 digits").optional().or(z.literal("")),
  status: z.enum(["active", "inactive", "on_hold"]),
  date_of_birth: z.string().optional().or(z.literal("")),
  diagnosis: z.string().optional().or(z.literal("")),
})

type PatientFormData = z.infer<typeof patientFormSchema>

interface PatientFormProps {
  mode: "create" | "edit"
  patient?: PatientResponse
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function PatientForm({ mode, patient, open, onOpenChange }: PatientFormProps) {
  const createPatient = useCreatePatient()
  const updatePatient = useUpdatePatient()

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<PatientFormData>({
    resolver: zodResolver(patientFormSchema),
    defaultValues: {
      first_name: "",
      last_name: "",
      email: "",
      phone: "",
      status: "active",
      date_of_birth: "",
      diagnosis: "",
    },
  })

  const status = watch("status")

  // Reset form when dialog opens/closes or patient changes
  useEffect(() => {
    if (open && mode === "edit" && patient) {
      reset({
        first_name: patient.first_name,
        last_name: patient.last_name,
        email: patient.email || "",
        phone: patient.phone || "",
        status: (patient.status as "active" | "inactive" | "on_hold") || "active",
        date_of_birth: patient.date_of_birth || "",
        diagnosis: patient.diagnosis || "",
      })
    } else if (open && mode === "create") {
      reset({
        first_name: "",
        last_name: "",
        email: "",
        phone: "",
        status: "active",
        date_of_birth: "",
        diagnosis: "",
      })
    }
  }, [open, mode, patient, reset])

  const onSubmit = async (data: PatientFormData) => {
    try {
      // Convert empty strings to undefined
      const payload = {
        first_name: data.first_name,
        last_name: data.last_name,
        email: data.email || undefined,
        phone: data.phone || undefined,
        status: data.status,
        date_of_birth: data.date_of_birth || undefined,
        diagnosis: data.diagnosis || undefined,
      }

      if (mode === "create") {
        await createPatient.mutateAsync(payload)
      } else if (patient) {
        await updatePatient.mutateAsync({ patientId: patient.id, data: payload })
      }

      // Close dialog on success
      onOpenChange(false)
      reset()
    } catch {
      // Error handling is done by the mutation hooks
      console.error("Patient form submission failed")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? "Add Patient" : "Edit Patient"}
          </DialogTitle>
          <DialogDescription>
            {mode === "create"
              ? "Enter patient information to create a new record."
              : "Update patient information."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          {/* First Name */}
          <div className="form-group">
            <Label htmlFor="first_name">
              First Name <span className="text-red-500">*</span>
            </Label>
            <Input
              id="first_name"
              {...register("first_name")}
              className={errors.first_name ? "border-red-500" : ""}
            />
            {errors.first_name && (
              <p className="text-sm text-red-500 mt-1">{errors.first_name.message}</p>
            )}
          </div>

          {/* Last Name */}
          <div className="form-group">
            <Label htmlFor="last_name">
              Last Name <span className="text-red-500">*</span>
            </Label>
            <Input
              id="last_name"
              {...register("last_name")}
              className={errors.last_name ? "border-red-500" : ""}
            />
            {errors.last_name && (
              <p className="text-sm text-red-500 mt-1">{errors.last_name.message}</p>
            )}
          </div>

          {/* Email */}
          <div className="form-group">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              {...register("email")}
              className={errors.email ? "border-red-500" : ""}
            />
            {errors.email && (
              <p className="text-sm text-red-500 mt-1">{errors.email.message}</p>
            )}
          </div>

          {/* Phone */}
          <div className="form-group">
            <Label htmlFor="phone">Phone</Label>
            <Input
              id="phone"
              type="tel"
              {...register("phone")}
              className={errors.phone ? "border-red-500" : ""}
              placeholder="(555) 123-4567"
            />
            {errors.phone && (
              <p className="text-sm text-red-500 mt-1">{errors.phone.message}</p>
            )}
          </div>

          {/* Status */}
          <div className="form-group">
            <Label htmlFor="status">Status</Label>
            <Select
              value={status}
              onValueChange={(value) =>
                setValue("status", value as "active" | "inactive" | "on_hold")
              }
            >
              <SelectTrigger id="status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="inactive">Inactive</SelectItem>
                <SelectItem value="on_hold">On Hold</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Date of Birth */}
          <div className="form-group">
            <Label htmlFor="date_of_birth">Date of Birth</Label>
            <Input
              id="date_of_birth"
              type="date"
              {...register("date_of_birth")}
              className={errors.date_of_birth ? "border-red-500" : ""}
            />
            {errors.date_of_birth && (
              <p className="text-sm text-red-500 mt-1">{errors.date_of_birth.message}</p>
            )}
          </div>

          {/* Diagnosis */}
          <div className="form-group">
            <Label htmlFor="diagnosis">Diagnosis</Label>
            <Input
              id="diagnosis"
              {...register("diagnosis")}
              className={errors.diagnosis ? "border-red-500" : ""}
            />
            {errors.diagnosis && (
              <p className="text-sm text-red-500 mt-1">{errors.diagnosis.message}</p>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                onOpenChange(false)
                reset()
              }}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting
                ? mode === "create"
                  ? "Creating..."
                  : "Updating..."
                : mode === "create"
                  ? "Create Patient"
                  : "Update Patient"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
