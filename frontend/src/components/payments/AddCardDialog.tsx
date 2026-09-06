// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AddCardDialog
 *
 * Collects a card for a client, and never sees it.
 *
 * The card fields are rendered by Stripe inside an iframe this application
 * cannot read, and `stripe.confirmSetup` posts them from the browser straight
 * to Stripe against the SetupIntent the backend minted. What comes back here
 * is a SetupIntent id, which is all `useCompleteCardSetup` sends on — the
 * backend then reads the attached card from Stripe itself. So no card number
 * passes through this process, this API, or the database, and there is no code
 * path in which one could.
 *
 * Stripe.js is initialised from the setup response rather than from anything
 * configured on this side: the publishable key and, where the deployment names
 * one, the account it acts for both arrive with the client secret they belong
 * to, so all three necessarily agree.
 */

"use client"

import { useEffect, useMemo, useState } from "react"
// The `/pure` entry point does not fetch Stripe.js on import — only when
// `loadStripe` is actually called. The default entry point injects the script
// as soon as the module is evaluated, which would pull a third-party script
// into every chart page whether or not anyone opens this dialog.
import { loadStripe } from "@stripe/stripe-js/pure"
import {
  Elements,
  PaymentElement,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/Toast"
import { useCompleteCardSetup, useStartCardSetup } from "@/hooks/usePayments"
import type { CardSetupResponse } from "@/types/payments"

interface AddCardDialogProps {
  patientId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** True when a card is already on file, so the copy says so. */
  replacing?: boolean
}

function CardSetupForm({
  patientId,
  clientSecret,
  replacing,
  onDone,
}: {
  patientId: string
  clientSecret: string
  replacing: boolean
  onDone: () => void
}) {
  const stripe = useStripe()
  const elements = useElements()
  const { showToast } = useToast()
  const completeSetup = useCompleteCardSetup()
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  const busy = confirming || completeSetup.isPending

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!stripe || !elements) return

    setError(null)
    setConfirming(true)
    // `redirect: "if_required"` keeps card entry on this page. A payment method
    // that genuinely needs a redirect would leave it, which is why the result
    // is read rather than assumed below.
    const result = await stripe.confirmSetup({
      elements,
      clientSecret,
      redirect: "if_required",
    })
    setConfirming(false)

    if (result.error) {
      // Stripe's message is written for the person holding the card; ours
      // would only be vaguer.
      setError(result.error.message ?? "The card could not be saved.")
      return
    }

    try {
      await completeSetup.mutateAsync({
        patientId,
        setupIntentId: result.setupIntent.id,
      })
      showToast(replacing ? "Card replaced." : "Card saved.", "success")
      onDone()
    } catch {
      setError(
        "The card reached the processor but could not be recorded here. Try again.",
      )
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <PaymentElement />
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" disabled={!stripe || busy}>
          {busy ? "Saving..." : replacing ? "Replace card" : "Save card"}
        </Button>
      </DialogFooter>
    </form>
  )
}

/**
 * One card-collection attempt.
 *
 * Mounted only while the dialog is open, which is what resets it: a SetupIntent
 * is single-use, so a reopened dialog has to start a fresh one rather than
 * reuse a client secret that may already be spent.
 */
function CardSetupFlow({
  patientId,
  replacing,
  onDone,
}: {
  patientId: string
  replacing: boolean
  onDone: () => void
}) {
  const { mutateAsync: startSetup } = useStartCardSetup()
  const [setup, setSetup] = useState<CardSetupResponse | null>(null)
  const [setupError, setSetupError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    startSetup({ patientId })
      .then((response) => {
        if (!cancelled) setSetup(response)
      })
      .catch(() => {
        if (!cancelled) {
          setSetupError("Card payments are not available right now. Try again shortly.")
        }
      })
    return () => {
      cancelled = true
    }
  }, [patientId, startSetup])

  const stripePromise = useMemo(() => {
    if (!setup) return null
    // The account option is passed only when the deployment named one —
    // sending it otherwise, or omitting it when it is needed, initialises
    // Stripe.js against the wrong account and card collection fails.
    return loadStripe(
      setup.publishable_key,
      setup.stripe_account_id ? { stripeAccount: setup.stripe_account_id } : undefined,
    )
  }, [setup])

  if (setupError) {
    return (
      <p role="alert" className="py-4 text-sm text-red-600">
        {setupError}
      </p>
    )
  }

  if (!setup || !stripePromise) {
    return <p className="py-4 text-sm text-neutral-500">Preparing secure card entry...</p>
  }

  return (
    <Elements stripe={stripePromise} options={{ clientSecret: setup.client_secret }}>
      <CardSetupForm
        patientId={patientId}
        clientSecret={setup.client_secret}
        replacing={replacing}
        onDone={onDone}
      />
    </Elements>
  )
}

export function AddCardDialog({
  patientId,
  open,
  onOpenChange,
  replacing = false,
}: AddCardDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{replacing ? "Replace card on file" : "Add a card"}</DialogTitle>
          <DialogDescription>
            Card details go straight to the payment processor. They are never
            sent to or stored by Pablo.
          </DialogDescription>
        </DialogHeader>
        {open && (
          <CardSetupFlow
            patientId={patientId}
            replacing={replacing}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
