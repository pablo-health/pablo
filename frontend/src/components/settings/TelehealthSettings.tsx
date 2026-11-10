// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where your sessions are held online.
 *
 * Shows every service this deployment offers and whether you have connected
 * it, rather than only the ones you have — a service left off the list
 * silently gives a reader nothing to act on, and "connect Zoom" is the whole
 * point of the screen.
 *
 * Pablo hosts no video of its own, so nothing here creates a room. It
 * connects the account you already have, or records the room you already
 * use, and the appointment asks for a link when you book one.
 */

"use client"

import { Suspense, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertCircle, Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  disconnectZoom,
  getZoomAuthUrl,
  isZoomRoomUrl,
  listTelehealthProviders,
  setDefaultVideoService,
  setTelehealthRoomUrl,
  setZoomRoomUrl,
} from "@/lib/api/telehealth"
import { queryKeys } from "@/lib/api/queryKeys"
import { ZoomConnectReturn } from "./ZoomConnectReturn"

const PROVIDERS_QUERY_KEY = ["telehealth", "providers"]

/**
 * Where Zoom sends the clinician back to, as a path.
 *
 * This settings section rather than `/dashboard/settings`, which has no page
 * of its own and server-redirects to the first item — dropping the query
 * string, and with it the authorization code, before anything could read it.
 *
 * Sent to the deployment when the authorization URL is built and again when
 * the code is spent, and the two have to be the same string.
 */
const ZOOM_RETURN_PATH = "/dashboard/settings/sessions"

const DOXY_ME = "doxy_me"
const ZOOM = "zoom"
// A link pasted onto one appointment is not a service sessions can default to.
const MANUAL = "manual"

const ROOM_URL_LABEL = "Your Doxy.me room"
const ROOM_URL_HELP = "Paste the address of your waiting room."
const ROOM_URL_INVALID = "That doesn't look like a web address. It should start with https://."
const ROOM_URL_FAILED = "That didn't save. Try again."
const ZOOM_ROOM_LABEL = "Your Zoom room"
const ZOOM_ROOM_HELP = "Paste the link to the Zoom room you hold sessions in."
const ZOOM_ROOM_INVALID = "That isn't a Zoom link. It should start with https:// and be on zoom.us."
const ZOOM_CONNECT_FAILED = "Couldn't start the Zoom connection. Try again."
const ZOOM_DISCONNECT_FAILED = "Couldn't disconnect. Try again."
const DEFAULT_FAILED = "That didn't save. Try again."

/** An https web address. What a doxy.me room has to be. */
function isWebAddress(value: string): boolean {
  return value.startsWith("https://")
}

export function TelehealthSettings() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const { data } = useQuery({
    queryKey: PROVIDERS_QUERY_KEY,
    queryFn: listTelehealthProviders,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: PROVIDERS_QUERY_KEY })

  const makeDefault = useMutation({
    mutationFn: (providerId: string) => setDefaultVideoService(providerId),
    onMutate: () => setError(null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: PROVIDERS_QUERY_KEY })
      void queryClient.invalidateQueries({ queryKey: queryKeys.user.preferences() })
    },
    onError: () => setError(DEFAULT_FAILED),
  })

  const disconnect = useMutation({
    mutationFn: disconnectZoom,
    onMutate: () => setError(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PROVIDERS_QUERY_KEY }),
    onError: () => setError(ZOOM_DISCONNECT_FAILED),
  })

  async function connectZoom() {
    setError(null)
    try {
      const { auth_url } = await getZoomAuthUrl(`${window.location.origin}${ZOOM_RETURN_PATH}`)
      window.location.assign(auth_url)
    } catch {
      setError(ZOOM_CONNECT_FAILED)
    }
  }

  const providers = data?.providers ?? []
  const zoom = providers.find((provider) => provider.id === ZOOM)
  const doxy = providers.find((provider) => provider.id === DOXY_ME)

  return (
    <div data-testid="telehealth-settings" className="space-y-4">
      {/* Reading the query string is what suspends, and the card should not
          wait on it. Renders nothing unless the clinician is coming back
          from Zoom, or the exchange failed. */}
      <Suspense fallback={null}>
        <ZoomConnectReturn
          providersQueryKey={PROVIDERS_QUERY_KEY}
          redirectUri={
            typeof window === "undefined" ? "" : `${window.location.origin}${ZOOM_RETURN_PATH}`
          }
        />
      </Suspense>

      <ul className="space-y-2">
        {providers.map((provider) => (
          <li
            key={provider.id}
            data-testid={`telehealth-provider-${provider.id}`}
            className="flex items-center justify-between gap-4 text-sm"
          >
            <span className="flex items-center gap-2 text-neutral-900">
              {provider.connected && (
                <Check className="h-4 w-4 text-secondary-600" aria-hidden="true" />
              )}
              {provider.display_name}
              {provider.id === data?.default_provider && (
                <span
                  data-testid={`telehealth-provider-${provider.id}-default`}
                  className="text-xs font-medium text-secondary-700"
                >
                  Default
                </span>
              )}
            </span>
            <span className="flex items-center gap-3">
              {provider.connected &&
                provider.id !== MANUAL &&
                provider.id !== data?.default_provider && (
                  <Button
                    variant="ghost"
                    size="sm"
                    data-testid={`telehealth-provider-${provider.id}-make-default`}
                    onClick={() => makeDefault.mutate(provider.id)}
                    disabled={makeDefault.isPending}
                  >
                    Make default
                  </Button>
                )}
              <span
                data-testid={`telehealth-provider-${provider.id}-state`}
                className="text-muted-foreground"
              >
                {provider.connected ? "Ready" : "Not set up"}
              </span>
            </span>
          </li>
        ))}
      </ul>

      {zoom && !data?.zoom_app && (
        <PastedRoomField
          id="telehealth-zoom-room-url"
          label={ZOOM_ROOM_LABEL}
          help={ZOOM_ROOM_HELP}
          placeholder="https://zoom.us/j/1234567890"
          saved={data?.zoom_room_url ?? null}
          isValid={isZoomRoomUrl}
          invalidMessage={ZOOM_ROOM_INVALID}
          save={setZoomRoomUrl}
          onSaved={refresh}
          setError={setError}
        />
      )}

      {zoom && data?.zoom_app && (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            {zoom.connected
              ? "Pablo makes a Zoom meeting for each appointment."
              : "Connect Zoom and Pablo will make a meeting for each appointment."}
          </p>
          {zoom.connected ? (
            <Button
              variant="ghost"
              size="sm"
              data-testid="telehealth-zoom-disconnect"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              data-testid="telehealth-zoom-connect"
              onClick={() => void connectZoom()}
            >
              Connect Zoom
            </Button>
          )}
        </div>
      )}

      {doxy && (
        <PastedRoomField
          id="telehealth-room-url"
          label={ROOM_URL_LABEL}
          help={ROOM_URL_HELP}
          placeholder="https://yourpractice.doxy.me/yourname"
          saved={data?.room_url ?? null}
          isValid={isWebAddress}
          invalidMessage={ROOM_URL_INVALID}
          save={setTelehealthRoomUrl}
          onSaved={refresh}
          setError={setError}
        />
      )}

      {error !== null && (
        <p data-testid="telehealth-settings-error" className="flex items-center gap-1.5 text-xs text-red-600">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  )
}

interface PastedRoomFieldProps {
  /** The input's id, and the stem of its test ids. */
  id: string
  label: string
  help: string
  placeholder: string
  saved: string | null
  isValid: (value: string) => boolean
  invalidMessage: string
  save: (value: string | null) => Promise<unknown>
  onSaved: () => void
  setError: (message: string | null) => void
}

/**
 * A room the clinician pastes once and every appointment on that service uses.
 *
 * Holds what has been typed, or null for "whatever is saved", rather than
 * seeding from the query, so the field follows the server until somebody
 * edits it and follows them afterwards. Empty saves as null: removing the
 * room, not saving an empty string.
 */
function PastedRoomField({
  id,
  label,
  help,
  placeholder,
  saved,
  isValid,
  invalidMessage,
  save,
  onSaved,
  setError,
}: PastedRoomFieldProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const value = draft ?? saved ?? ""

  const mutation = useMutation({
    mutationFn: (next: string | null) => save(next),
    onMutate: () => setError(null),
    onSuccess: () => {
      setDraft(null)
      onSaved()
    },
    onError: () => setError(ROOM_URL_FAILED),
  })

  function submit() {
    const trimmed = value.trim()
    if (trimmed !== "" && !isValid(trimmed)) {
      setError(invalidMessage)
      return
    }
    mutation.mutate(trimmed === "" ? null : trimmed)
  }

  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          data-testid={id}
          type="url"
          value={value}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          variant="outline"
          size="sm"
          data-testid={`${id}-save`}
          onClick={submit}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{help}</p>
    </div>
  )
}
