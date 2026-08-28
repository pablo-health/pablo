// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { ReactElement, ReactNode } from "react"
import { vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  render,
  type RenderOptions,
  type RenderResult,
} from "@testing-library/react"
import { ToastProvider } from "@/components/ui/Toast"
import { ConfigProvider, type RuntimeConfig } from "@/lib/config-provider"

export interface RenderWithProvidersOptions extends Omit<RenderOptions, "wrapper"> {
  /**
   * Set this to give a consumer of useConfig() a config to read. When set,
   * the helper stubs the /api/config fetch so ConfigProvider resolves
   * without a real network call. Leave unset for slots that don't touch
   * runtime config. AuthProvider is never included here — useAuth() already
   * returns a safe default outside of AuthProvider, so consumers that only
   * need auth don't need this helper at all.
   */
  config?: RuntimeConfig
}

export interface RenderWithProvidersResult extends RenderResult {
  queryClient: QueryClient
}

function stubConfigFetch(config: RuntimeConfig) {
  globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString()
    if (url.endsWith("/api/config")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(config),
      } as Response)
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({}),
    } as Response)
  }) as unknown as typeof fetch
}

export function renderWithProviders(
  ui: ReactElement,
  options: RenderWithProvidersOptions = {},
): RenderWithProvidersResult {
  const { config, ...renderOptions } = options

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })

  if (config) {
    stubConfigFetch(config)
  }

  function Wrapper({ children }: { children: ReactNode }) {
    const body = config ? <ConfigProvider>{children}</ConfigProvider> : children
    return (
      <ToastProvider>
        <QueryClientProvider client={queryClient}>{body}</QueryClientProvider>
      </ToastProvider>
    )
  }

  const result = render(ui, { wrapper: Wrapper, ...renderOptions })
  return { ...result, queryClient }
}
