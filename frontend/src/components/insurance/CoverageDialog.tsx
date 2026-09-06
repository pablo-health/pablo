// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * CoverageDialog
 *
 * Add or edit a client's coverage. The payer picker lists what the practice
 * already files with and falls back to free text — typing a payer off the
 * card adds it to the practice's list on the way through. Subscriber
 * details only appear when the subscriber is somebody other than the client.
 */

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
import { useCreateCoverage, usePayers, useUpdateCoverage } from "@/hooks/useCoverage"
import type { CoverageResponse, CreateCoverageRequest } from "@/types/coverage"

/** Radix `Select.Item` rejects an empty value, so "type a new payer" needs a
 * sentinel distinct from every payer row id. */
const NEW_PAYER = "__new__"

const schema = z
  .object({
    payer_choice: z.string().min(1, "Pick a payer"),
    new_payer_name: z.string().max(255),
    new_payer_id: z.string().max(80),
    member_id: z.string().min(1, "Member ID is required").max(80),
    group_number: z.string().max(80),
    plan_name: z.string().max(255),
    subscriber_relationship: z.enum(["self", "spouse", "child", "other"]),
    subscriber_first_name: z.string().max(255),
    subscriber_last_name: z.string().max(255),
    subscriber_date_of_birth: z.string(),
    subscriber_sex: z.enum(["M", "F", "U", ""]),
    subscriber_address_line1: z.string().max(255),
    subscriber_city: z.string().max(100),
    subscriber_state: z.string().max(2),
    subscriber_postal_code: z.string().max(10),
  })
  .refine((v) => v.payer_choice !== NEW_PAYER || v.new_payer_name.trim().length > 0, {
    message: "Insurance company name is required",
    path: ["new_payer_name"],
  })

type FormData = z.infer<typeof schema>

const EMPTY: FormData = {
  payer_choice: "",
  new_payer_name: "",
  new_payer_id: "",
  member_id: "",
  group_number: "",
  plan_name: "",
  subscriber_relationship: "self",
  subscriber_first_name: "",
  subscriber_last_name: "",
  subscriber_date_of_birth: "",
  subscriber_sex: "",
  subscriber_address_line1: "",
  subscriber_city: "",
  subscriber_state: "",
  subscriber_postal_code: "",
}

function fromCoverage(coverage: CoverageResponse): FormData {
  return {
    ...EMPTY,
    payer_choice: coverage.payer.id,
    member_id: coverage.member_id,
    group_number: coverage.group_number ?? "",
    plan_name: coverage.plan_name ?? "",
    subscriber_relationship: coverage.subscriber_relationship,
    subscriber_first_name: coverage.subscriber_first_name ?? "",
    subscriber_last_name: coverage.subscriber_last_name ?? "",
    subscriber_date_of_birth: coverage.subscriber_date_of_birth ?? "",
    subscriber_sex: coverage.subscriber_sex ?? "",
    subscriber_address_line1: coverage.subscriber_address_line1 ?? "",
    subscriber_city: coverage.subscriber_city ?? "",
    subscriber_state: coverage.subscriber_state ?? "",
    subscriber_postal_code: coverage.subscriber_postal_code ?? "",
  }
}

const orNull = (value: string) => value.trim() || null

interface CoverageDialogProps {
  patientId: string
  /** The plan being edited; `null` adds a new one. */
  coverage: CoverageResponse | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CoverageDialog({ patientId, coverage, open, onOpenChange }: CoverageDialogProps) {
  const { data: payers } = usePayers()
  const create = useCreateCoverage()
  const update = useUpdateCoverage()
  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({ resolver: zodResolver(schema), defaultValues: EMPTY })

  useEffect(() => {
    if (open) reset(coverage ? fromCoverage(coverage) : EMPTY)
  }, [open, coverage, reset])

  const payerChoice = watch("payer_choice")
  const relationship = watch("subscriber_relationship")
  const subscriberSex = watch("subscriber_sex")
  const isSelf = relationship === "self"

  async function onSubmit(data: FormData) {
    const subscriber = {
      subscriber_relationship: data.subscriber_relationship,
      subscriber_first_name: isSelf ? null : orNull(data.subscriber_first_name),
      subscriber_last_name: isSelf ? null : orNull(data.subscriber_last_name),
      subscriber_date_of_birth: isSelf ? null : orNull(data.subscriber_date_of_birth),
      subscriber_sex: isSelf ? null : data.subscriber_sex || null,
      subscriber_address_line1: isSelf ? null : orNull(data.subscriber_address_line1),
      subscriber_city: isSelf ? null : orNull(data.subscriber_city),
      subscriber_state: isSelf ? null : orNull(data.subscriber_state),
      subscriber_postal_code: isSelf ? null : orNull(data.subscriber_postal_code),
    }
    const plan = {
      member_id: data.member_id.trim(),
      group_number: orNull(data.group_number),
      plan_name: orNull(data.plan_name),
      ...subscriber,
    }
    try {
      if (coverage) {
        await update.mutateAsync({
          patientId,
          data: { ...plan, payer_id: data.payer_choice },
        })
      } else {
        const payload: CreateCoverageRequest =
          data.payer_choice === NEW_PAYER
            ? {
                ...plan,
                new_payer: {
                  name: data.new_payer_name.trim(),
                  payer_id: data.new_payer_id.trim() || "UNKNOWN",
                },
              }
            : { ...plan, payer_id: data.payer_choice }
        await create.mutateAsync({ patientId, data: payload })
      }
      onOpenChange(false)
    } catch {
      // The mutation hooks surface the error state; the dialog stays open.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{coverage ? "Edit coverage" : "Add coverage"}</DialogTitle>
          <DialogDescription>Copy the details from the client&apos;s insurance card.</DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="form-group">
            <Label htmlFor="payer_choice">Insurance company</Label>
            <Select value={payerChoice} onValueChange={(v) => setValue("payer_choice", v)}>
              <SelectTrigger id="payer_choice" aria-label="Insurance company">
                <SelectValue placeholder="Pick a payer" />
              </SelectTrigger>
              <SelectContent>
                {payers?.data.map((payer) => (
                  <SelectItem key={payer.id} value={payer.id}>
                    {payer.name} · {payer.payer_id}
                  </SelectItem>
                ))}
                {!coverage && <SelectItem value={NEW_PAYER}>Add a payer from the card…</SelectItem>}
              </SelectContent>
            </Select>
            {errors.payer_choice && (
              <p className="mt-1 text-sm text-red-500">{errors.payer_choice.message}</p>
            )}
          </div>

          {payerChoice === NEW_PAYER && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="form-group">
                <Label htmlFor="new_payer_name">Company name</Label>
                <Input id="new_payer_name" {...register("new_payer_name")} />
                {errors.new_payer_name && (
                  <p className="mt-1 text-sm text-red-500">{errors.new_payer_name.message}</p>
                )}
              </div>
              <div className="form-group">
                <Label htmlFor="new_payer_id">Payer ID (from the card)</Label>
                <Input id="new_payer_id" {...register("new_payer_id")} placeholder="e.g. 60054" />
              </div>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="form-group">
              <Label htmlFor="member_id">Member ID</Label>
              <Input id="member_id" {...register("member_id")} />
              {errors.member_id && (
                <p className="mt-1 text-sm text-red-500">{errors.member_id.message}</p>
              )}
            </div>
            <div className="form-group">
              <Label htmlFor="group_number">Group number</Label>
              <Input id="group_number" {...register("group_number")} />
            </div>
          </div>

          <div className="form-group">
            <Label htmlFor="plan_name">Plan name</Label>
            <Input id="plan_name" {...register("plan_name")} />
          </div>

          <div className="form-group">
            <Label htmlFor="subscriber_relationship">Client&apos;s relationship to the subscriber</Label>
            <Select
              value={relationship}
              onValueChange={(v) =>
                setValue("subscriber_relationship", v as FormData["subscriber_relationship"])
              }
            >
              <SelectTrigger id="subscriber_relationship" aria-label="Relationship to subscriber">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="self">Self — the client is the subscriber</SelectItem>
                <SelectItem value="spouse">Spouse</SelectItem>
                <SelectItem value="child">Child</SelectItem>
                <SelectItem value="other">Other</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {!isSelf && (
            <fieldset className="space-y-4 rounded-lg border border-neutral-100 p-3">
              <legend className="px-1 text-xs font-medium text-neutral-600">Subscriber</legend>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="form-group">
                  <Label htmlFor="subscriber_first_name">First name</Label>
                  <Input id="subscriber_first_name" {...register("subscriber_first_name")} />
                </div>
                <div className="form-group">
                  <Label htmlFor="subscriber_last_name">Last name</Label>
                  <Input id="subscriber_last_name" {...register("subscriber_last_name")} />
                </div>
                <div className="form-group">
                  <Label htmlFor="subscriber_date_of_birth">Date of birth</Label>
                  <Input
                    id="subscriber_date_of_birth"
                    type="date"
                    {...register("subscriber_date_of_birth")}
                  />
                </div>
                <div className="form-group">
                  <Label htmlFor="subscriber_sex">Sex on insurance card</Label>
                  <Select
                    value={subscriberSex}
                    onValueChange={(v) => setValue("subscriber_sex", v as FormData["subscriber_sex"])}
                  >
                    <SelectTrigger id="subscriber_sex" aria-label="Subscriber sex">
                      <SelectValue placeholder="Not set" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="M">Male</SelectItem>
                      <SelectItem value="F">Female</SelectItem>
                      <SelectItem value="U">Unknown</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="form-group">
                <Label htmlFor="subscriber_address_line1">Address</Label>
                <Input id="subscriber_address_line1" {...register("subscriber_address_line1")} />
              </div>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="form-group">
                  <Label htmlFor="subscriber_city">City</Label>
                  <Input id="subscriber_city" {...register("subscriber_city")} />
                </div>
                <div className="form-group">
                  <Label htmlFor="subscriber_state">State</Label>
                  <Input id="subscriber_state" maxLength={2} {...register("subscriber_state")} />
                </div>
                <div className="form-group">
                  <Label htmlFor="subscriber_postal_code">ZIP</Label>
                  <Input id="subscriber_postal_code" {...register("subscriber_postal_code")} />
                </div>
              </div>
            </fieldset>
          )}

          {(create.isError || update.isError) && (
            <p className="text-sm text-red-500">Could not save the coverage. Please try again.</p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : coverage ? "Save changes" : "Add coverage"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
