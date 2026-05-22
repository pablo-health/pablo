// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Dev mount for ``<ChatPanel />`` (§13 bundle dogfooding).
 *
 * Production-gated: ``NODE_ENV === "production"`` short-circuits to a
 * 404 so this route never ships in the deployed image.
 *
 * Visit ``/dev/chat?patient_id=<uuid>`` to mount the panel against a
 * live patient for SSE + manifest preview testing. Optional
 * ``feature_key`` and ``prompt`` query params override the defaults.
 */

import { notFound } from "next/navigation"

import { DevChatMount } from "./DevChatMount"

interface DevChatPageProps {
  searchParams: Promise<{
    patient_id?: string
    feature_key?: string
    prompt?: string
  }>
}

export const dynamic = "force-dynamic"

export default async function DevChatPage({ searchParams }: DevChatPageProps) {
  if (process.env.NODE_ENV === "production") {
    notFound()
  }

  const params = await searchParams
  const patientId = params.patient_id?.trim() ?? ""
  const callerFeatureKey = params.feature_key?.trim() || "dev_chat"
  // Empty string (when the URL doesn't supply ?prompt=...) signals the
  // backend to resolve the prompt server-side via
  // backend.app.prompts.chat.get_chat_system_prompt(user.provider_type).
  // This is the single source of truth — the dev mount no longer
  // hardcodes a prompt literal that would drift from production.
  const callerSystemPrompt = params.prompt?.trim() ?? ""

  return (
    <main className="mx-auto h-dvh max-w-3xl px-4 py-6">
      <header className="mb-4">
        <h1 className="font-display text-xl font-semibold text-neutral-900">
          Dev chat
        </h1>
        <p className="text-xs text-neutral-500">
          NODE_ENV-gated mount for chat dogfooding. Pass{" "}
          <code className="font-mono">?patient_id=…</code> to start.
        </p>
      </header>
      <DevChatMount
        patientId={patientId}
        callerFeatureKey={callerFeatureKey}
        callerSystemPrompt={callerSystemPrompt}
      />
    </main>
  )
}
