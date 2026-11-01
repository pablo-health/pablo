// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"
import { PortalBootLoading } from "@/components/portal-shell/PortalBootLoading"
import { ConfigProvider } from "@/lib/config-provider"
import { isUnder } from "@/lib/portal-host/routing"
import { AuthProvider } from "@/lib/auth-context"
import { ApiError } from "@/lib/api/client"
import { ToastProvider, useToast } from "@/components/ui/Toast"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { installGlobalErrorReporter } from "@/lib/feErrorReporter"
import { OidcSessionProviderWrapper } from "@/lib/auth/oidc/SessionProviderWrapper"
import { outerProviderWrappers } from "./providers.extensions"

export function Providers({
  children,
  portalOnlyHost = false,
}: {
  children: React.ReactNode
  /** The request's host serves nothing but the patient portal (`servesOnlyPortal`). */
  portalOnlyHost?: boolean
}) {
  useEffect(() => {
    installGlobalErrorReporter()
  }, [])

  // ToastProvider sits outside QueryClientProvider so the mutation cache
  // (constructed below) can route every mutation failure through the same
  // toast surface components already use, with no per-call-site opt-in.
  return (
    <ToastProvider>
      <QueryProviders portalOnlyHost={portalOnlyHost}>{children}</QueryProviders>
    </ToastProvider>
  )
}

/**
 * Whether the page being loaded is the patient portal: anywhere on a host that
 * serves only the portal, or under `/portal` on this deployment's own host.
 */
export function isPortalPage(pathname: string | null, portalOnlyHost: boolean): boolean {
  return portalOnlyHost || (pathname !== null && isUnder(pathname, "/portal"))
}

/**
 * A mutation cache with a global onError so a mutation that defines no
 * onError of its own still surfaces its failure — silence is no longer a
 * valid default. Call sites that want bespoke copy can still add their own
 * onError; that one runs in addition to this global one.
 */
/** Client errors that can still succeed when asked again. */
const RETRYABLE_CLIENT_STATUSES = new Set([408, 429])

/**
 * Query retry policy: up to three retries, except for a client error the
 * server has already decided — a 403 or 404 answers the same way every time.
 * Retrying those only held a panel in its loading state for several seconds
 * before its "didn't load" message and support line could show, which read
 * as an empty page.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount >= 3) return false
  const status = error instanceof ApiError ? error.status : undefined
  if (status !== undefined && status >= 400 && status < 500) {
    return RETRYABLE_CLIENT_STATUSES.has(status)
  }
  return true
}

export function createAppQueryClient(showToast: (message: string) => void): QueryClient {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error) => {
        showToast(error.message || "Something went wrong. Please try again.")
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 60 * 1000,
        refetchOnWindowFocus: false,
        retry: shouldRetryQuery,
      },
    },
  })
}

function QueryProviders({ children, portalOnlyHost }: { children: React.ReactNode; portalOnlyHost: boolean }) {
  const { showToast } = useToast()
  const pathname = usePathname()

  const [queryClient] = useState(() => createAppQueryClient(showToast))

  // OidcSessionProviderWrapper is a no-op when the active provider is not
  // `oidc` — the Firebase path is unchanged at runtime.
  const core = (
    <OidcSessionProviderWrapper>
      {/* A portal visitor waits on the portal's own quiet screen, not the clinician app's. */}
      <ConfigProvider loading={isPortalPage(pathname, portalOnlyHost) ? <PortalBootLoading /> : undefined}>
        <AuthProvider>
          <ThemeProvider>
            {children}
            <ReactQueryDevtools initialIsOpen={false} />
          </ThemeProvider>
        </AuthProvider>
      </ConfigProvider>
    </OidcSessionProviderWrapper>
  )

  // Downstream builds may wrap the whole app in extra providers (e.g. one that
  // must render even when the auth/config providers below it are degraded).
  // Applied outermost-first, just inside QueryClientProvider.
  return (
    <QueryClientProvider client={queryClient}>
      {outerProviderWrappers.reduceRight(
        (acc, Wrapper) => (
          <Wrapper>{acc}</Wrapper>
        ),
        core
      )}
    </QueryClientProvider>
  )
}
