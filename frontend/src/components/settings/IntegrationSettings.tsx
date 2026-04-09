// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  configureICalSync,
  disconnectICalSync,
  getICalSyncStatus,
  importClients,
  type ICalConnectionStatus,
  type ImportClientsResponse,
} from "@/lib/api/scheduling"
import {
  AlertCircle,
  Check,
  Link2Off,
  Loader2,
  Upload,
} from "lucide-react"

const EHR_OPTIONS = [
  { value: "simplepractice", label: "SimplePractice" },
  { value: "sessions_health", label: "Sessions Health" },
] as const

const URL_HINTS: Record<string, string> = {
  simplepractice:
    "Find this in SimplePractice: Settings > Calendar > Apple Calendar section. For best client matching, set calendar display to show full names.",
  sessions_health:
    "Find this in Sessions Health: Settings > Calendar Integration > iCal Feed URL.",
}

export function IntegrationSettings() {
  const [connections, setConnections] = useState<ICalConnectionStatus[]>([])
  const [loaded, setLoaded] = useState(false)
  const [selectedEhr, setSelectedEhr] = useState<string>(EHR_OPTIONS[0].value)
  const [feedUrl, setFeedUrl] = useState("")
  const [connecting, setConnecting] = useState(false)
  const [connectResult, setConnectResult] = useState<string | null>(null)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<ImportClientsResponse | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadStatus = useCallback(async () => {
    try {
      const status = await getICalSyncStatus()
      setConnections(status.connections)
    } catch {
      // Silently fail — not critical
    }
    setLoaded(true)
  }, [])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  const handleConnect = async () => {
    setConnecting(true)
    setConnectResult(null)
    setConnectError(null)
    try {
      const result = await configureICalSync(selectedEhr, feedUrl)
      setConnectResult(`Connected! Found ${result.event_count} appointments.`)
      setFeedUrl("")
      loadStatus()
    } catch (err) {
      setConnectError(
        err instanceof Error ? err.message : "Failed to connect"
      )
    } finally {
      setConnecting(false)
    }
  }

  const handleDisconnect = async (ehrSystem: string) => {
    try {
      await disconnectICalSync(ehrSystem)
      loadStatus()
    } catch {
      // Handle silently
    }
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setImporting(true)
    setImportResult(null)
    try {
      const result = await importClients(selectedEhr, file)
      setImportResult(result)
      loadStatus()
    } catch (err) {
      setImportResult({
        imported: 0,
        updated: 0,
        skipped: 0,
        mappings_created: 0,
        errors: [err instanceof Error ? err.message : "Import failed"],
      })
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const connectedSystems = new Set(connections.map((c) => c.ehr_system))
  const availableEhr = EHR_OPTIONS.filter(
    (opt) => !connectedSystems.has(opt.value)
  )

  return (
    <div className="space-y-5">
      {/* Active connections */}
      {connections.length > 0 && (
        <div className="space-y-3">
          <Label className="text-sm font-medium text-neutral-700">
            Connected
          </Label>
          {connections.map((conn) => {
            const label =
              EHR_OPTIONS.find((o) => o.value === conn.ehr_system)?.label ??
              conn.ehr_system
            return (
              <div
                key={conn.ehr_system}
                className="flex items-center justify-between rounded-lg border border-neutral-200 px-4 py-3"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-green-500" />
                    <span className="text-sm font-medium">{label}</span>
                  </div>
                  {conn.last_synced_at && (
                    <p className="text-xs text-neutral-500 mt-0.5">
                      Last synced:{" "}
                      {new Date(conn.last_synced_at).toLocaleString()}
                    </p>
                  )}
                  {conn.last_sync_error && (
                    <p className="text-xs text-red-500 mt-0.5">
                      {conn.last_sync_error}
                    </p>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleDisconnect(conn.ehr_system)}
                  aria-label={`Disconnect ${label}`}
                >
                  <Link2Off className="h-4 w-4 mr-1" />
                  Disconnect
                </Button>
              </div>
            )
          })}
        </div>
      )}

      {/* Add new connection */}
      {availableEhr.length > 0 && (
        <div className="space-y-3">
          <Label className="text-sm font-medium text-neutral-700">
            {connections.length > 0
              ? "Add Another Connection"
              : "Connect EHR Calendar"}
          </Label>

          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="ehr-system" className="text-xs text-neutral-500">
                EHR System
              </Label>
              <select
                id="ehr-system"
                value={selectedEhr}
                onChange={(e) => {
                  setSelectedEhr(e.target.value)
                  setConnectResult(null)
                  setConnectError(null)
                }}
                className="rounded-md border border-neutral-200 px-3 py-2 text-sm"
              >
                {availableEhr.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="feed-url" className="text-xs text-neutral-500">
                iCal Feed URL
              </Label>
              <Input
                id="feed-url"
                value={feedUrl}
                onChange={(e) => setFeedUrl(e.target.value)}
                placeholder="https://..."
                type="url"
              />
              <p className="text-xs text-neutral-400">
                {URL_HINTS[selectedEhr]}
              </p>
            </div>

            <Button
              size="sm"
              onClick={handleConnect}
              disabled={connecting || !feedUrl.trim()}
              className="w-fit"
            >
              {connecting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                  Connecting...
                </>
              ) : (
                "Connect"
              )}
            </Button>

            {connectResult && (
              <div className="flex items-center gap-1.5 text-sm text-green-600">
                <Check className="h-4 w-4" />
                {connectResult}
              </div>
            )}
            {connectError && (
              <div className="flex items-center gap-1.5 text-sm text-red-600">
                <AlertCircle className="h-4 w-4" />
                {connectError}
              </div>
            )}
          </div>
        </div>
      )}

      {/* CSV/Zip Import */}
      <div className="space-y-3 border-t border-neutral-100 pt-4">
        <Label className="text-sm font-medium text-neutral-700">
          Import Clients
        </Label>
        <p className="text-xs text-neutral-400">
          Upload a client export (CSV or zip) from your EHR to import patients
          and auto-create client mappings.
        </p>
        <div className="flex items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.zip"
            onChange={handleFileUpload}
            className="hidden"
            id="client-import"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
          >
            {importing ? (
              <>
                <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                Importing...
              </>
            ) : (
              <>
                <Upload className="h-4 w-4 mr-1.5" />
                Upload Export
              </>
            )}
          </Button>
        </div>
        {importResult && (
          <div className="text-sm space-y-1">
            {importResult.errors.length > 0 ? (
              <p className="text-red-600">
                <AlertCircle className="h-4 w-4 inline mr-1" />
                {importResult.errors[0]}
              </p>
            ) : (
              <p className="text-green-600">
                <Check className="h-4 w-4 inline mr-1" />
                {importResult.imported} imported
                {importResult.updated > 0 && `, ${importResult.updated} updated`}
                , {importResult.skipped} unchanged
                {importResult.mappings_created > 0 &&
                  `, ${importResult.mappings_created} client mappings created`}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
