// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UploadTranscriptDialog Component
 *
 * File upload dialog with drag & drop, patient selection, and date picker.
 * Validates file format/size and uploads transcript for SOAP generation.
 */

"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Upload, X, FileText, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { usePatientList } from "@/hooks/usePatients"
import { useUploadSession } from "@/hooks/useSessions"
import { parseTranscriptFile } from "@/lib/utils/transcriptParser"
import {
  validateTranscriptFile,
  getFileExtension,
  formatFileSize,
} from "@/lib/utils/fileValidation"
import type { SessionResponse, TranscriptFormat } from "@/types/sessions"

export interface UploadTranscriptDialogProps {
  trigger?: React.ReactNode
  onSuccess?: (session: SessionResponse) => void
  className?: string
}

const uploadSchema = z.object({
  patient_id: z.string().min(1, "Patient is required"),
  session_date: z.string().min(1, "Session date is required"),
  transcript_file: z.custom<File>((val) => val instanceof File, "File is required"),
})

type UploadFormData = z.infer<typeof uploadSchema>

export function UploadTranscriptDialog({
  trigger,
  onSuccess,
  className,
}: UploadTranscriptDialogProps) {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const { data: patientsData, isLoading: isLoadingPatients } = usePatientList()
  const uploadMutation = useUploadSession()

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors },
  } = useForm<UploadFormData>({
    resolver: zodResolver(uploadSchema),
  })

  // eslint-disable-next-line react-hooks/incompatible-library -- React Hook Form's watch() is designed for this usage
  const watchedPatientId = watch("patient_id")

  const handleFileSelect = (file: File | null) => {
    if (!file) {
      setSelectedFile(null)
      setFileError(null)
      setValue("transcript_file", undefined as unknown as File)
      return
    }

    const validation = validateTranscriptFile(file)
    if (!validation.valid) {
      setFileError(validation.error!)
      setSelectedFile(null)
      setValue("transcript_file", undefined as unknown as File)
      return
    }

    setSelectedFile(file)
    setFileError(null)
    setValue("transcript_file", file)
  }

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)

    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      handleFileSelect(files[0])
    }
  }

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) {
      handleFileSelect(files[0])
    }
  }

  const onSubmit = async (data: UploadFormData) => {
    setUploadError(null)

    try {
      // Parse file content
      const transcript = await parseTranscriptFile(data.transcript_file)

      // Upload session
      const session = await uploadMutation.mutateAsync({
        patientId: data.patient_id,
        data: {
          patient_id: data.patient_id,
          session_date: data.session_date,
          transcript,
        },
      })

      // Success
      setOpen(false)
      reset()
      setSelectedFile(null)
      setFileError(null)

      if (onSuccess) {
        onSuccess(session)
      } else {
        // Navigate to session detail by default
        router.push(`/dashboard/sessions/${session.id}`)
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Failed to upload session"
      setUploadError(message)
    }
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      // Reset form when closing
      reset()
      setSelectedFile(null)
      setFileError(null)
      setUploadError(null)
    }
    setOpen(newOpen)
  }

  const getFormatBadge = () => {
    if (!selectedFile) return null
    const ext = getFileExtension(selectedFile.name).replace(".", "").toUpperCase()
    return (
      <span className="px-2 py-1 bg-primary-100 text-primary-700 text-xs font-medium rounded">
        {ext}
      </span>
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {trigger ? (
        <DialogTrigger asChild>{trigger}</DialogTrigger>
      ) : (
        <DialogTrigger asChild>
          <Button>
            <Upload className="w-4 h-4 mr-2" />
            Upload Session
          </Button>
        </DialogTrigger>
      )}

      <DialogContent className={cn("sm:max-w-[600px]", className)}>
        <DialogHeader>
          <DialogTitle>Upload Session Transcript</DialogTitle>
          <DialogDescription>
            Upload a transcript to generate a SOAP note. Accepted formats: VTT, JSON, TXT
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
          {/* Patient Selection */}
          <div className="space-y-2">
            <Label htmlFor="patient_id">
              Patient <span className="text-destructive">*</span>
            </Label>
            <Select
              value={watchedPatientId || ""}
              onValueChange={(value) => setValue("patient_id", value)}
              disabled={isLoadingPatients}
            >
              <SelectTrigger
                id="patient_id"
                className={cn(errors.patient_id && "border-destructive")}
              >
                <SelectValue placeholder="Select a patient..." />
              </SelectTrigger>
              <SelectContent>
                {patientsData?.data.map((patient) => (
                  <SelectItem key={patient.id} value={patient.id}>
                    {patient.last_name}, {patient.first_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.patient_id && (
              <p className="text-sm text-destructive">{errors.patient_id.message}</p>
            )}
          </div>

          {/* Session Date */}
          <div className="space-y-2">
            <Label htmlFor="session_date">
              Session Date & Time <span className="text-destructive">*</span>
            </Label>
            <Input
              id="session_date"
              type="datetime-local"
              {...register("session_date")}
              className={cn(errors.session_date && "border-destructive")}
            />
            {errors.session_date && (
              <p className="text-sm text-destructive">{errors.session_date.message}</p>
            )}
          </div>

          {/* File Upload */}
          <div className="space-y-2">
            <Label htmlFor="transcript_file">
              Transcript File <span className="text-destructive">*</span>
            </Label>

            {/* Drag & Drop Zone */}
            <div
              onDragEnter={handleDragEnter}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={cn(
                "relative border-2 border-dashed rounded-lg p-8 text-center transition-colors",
                isDragging && "border-primary bg-primary-50",
                !isDragging && !fileError && "border-neutral-300 hover:border-neutral-400",
                fileError && "border-destructive bg-destructive-50"
              )}
            >
              <input
                type="file"
                id="transcript_file"
                accept=".vtt,.json,.txt"
                onChange={handleFileInputChange}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
              />

              {selectedFile ? (
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <FileText className="w-8 h-8 text-primary-600" />
                    <div className="text-left">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-neutral-900">
                          {selectedFile.name}
                        </p>
                        {getFormatBadge()}
                      </div>
                      <p className="text-xs text-neutral-600">
                        {formatFileSize(selectedFile.size)}
                      </p>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleFileSelect(null)
                    }}
                  >
                    <X className="w-4 h-4" />
                  </Button>
                </div>
              ) : (
                <div>
                  <Upload
                    className={cn(
                      "w-12 h-12 mx-auto mb-4",
                      fileError ? "text-destructive" : "text-neutral-400"
                    )}
                  />
                  <p className="text-sm font-medium text-neutral-700">
                    {isDragging ? "Drop file here" : "Drag & drop a file or click to browse"}
                  </p>
                  <p className="text-xs text-neutral-500 mt-1">
                    Accepted formats: .vtt, .json, .txt (max 10MB)
                  </p>
                </div>
              )}
            </div>

            {fileError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="w-4 h-4" />
                <span>{fileError}</span>
              </div>
            )}
            {errors.transcript_file && (
              <p className="text-sm text-destructive">{errors.transcript_file.message}</p>
            )}
          </div>

          {/* Upload Error */}
          {uploadError && (
            <div className="flex items-center gap-2 p-3 text-sm text-destructive bg-destructive-50 border border-destructive rounded-md">
              <AlertCircle className="w-4 h-4" />
              <span>{uploadError}</span>
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={uploadMutation.isPending}>
              {uploadMutation.isPending ? "Uploading..." : "Upload & Generate SOAP"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
