// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { mockUser } from "@/lib/mockData"
import { authConfig } from "@/lib/auth-config"
import { CompliancePanel } from "@/components/compliance/CompliancePanel"

const IS_DEV_MODE = process.env.DEV_MODE === "true"

export default async function DashboardPage() {
  let user

  if (IS_DEV_MODE) {
    user = mockUser
  } else {
    const tokens = await getTokens(await cookies(), authConfig)
    const decodedToken = tokens?.decodedToken
    user = {
      name: decodedToken?.name || decodedToken?.email,
      email: decodedToken?.email,
      image: decodedToken?.picture,
    }
  }

  const formattedDate = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-bold text-neutral-900">
          {greetingFor()}, {user?.name?.split(" ")[0]}
        </h1>
        <p className="text-neutral-600 mt-2">{formattedDate}</p>
      </div>

      <CompliancePanel />
    </div>
  )
}

function greetingFor(): string {
  const hour = new Date().getHours()
  if (hour < 12) return "Good morning"
  if (hour < 18) return "Good afternoon"
  return "Good evening"
}
