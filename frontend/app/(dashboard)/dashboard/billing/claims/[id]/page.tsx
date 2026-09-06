// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { use } from "react"
import Link from "next/link"
import { ArrowLeft } from "lucide-react"
import { ClaimDetail } from "@/components/billing/claims/ClaimDetail"

interface ClaimPageProps {
  params: Promise<{ id: string }>
}

export default function ClaimPage({ params }: ClaimPageProps) {
  const { id } = use(params)
  return (
    <div className="space-y-6">
      <Link
        href="/dashboard/billing"
        className="flex items-center gap-2 text-neutral-600 hover:text-neutral-900 transition-colors"
      >
        <ArrowLeft className="w-5 h-5" />
        <span>Back to Billing</span>
      </Link>
      <ClaimDetail claimId={id} />
    </div>
  )
}
