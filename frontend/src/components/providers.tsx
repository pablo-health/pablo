// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { useEffect, useState } from "react"
import { ConfigProvider } from "@/lib/config-provider"
import { AuthProvider } from "@/lib/auth-context"
import { ToastProvider, useToast } from "@/components/ui/Toast"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { installGlobalErrorReporter } from "@/lib/feErrorReporter"
import { OidcSessionProviderWrapper } from "@/lib/auth/oidc/SessionProviderWrapper"
import { outerProviderWrappers } from "./providers.extensions"

export function Providers({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    installGlobalErrorReporter()
  }, [])

  // ToastProvider sits outside QueryClientProvider so the mutation cache
  // (constructed below) can route every mutation failure through the same
  // toast surface components already use, with no per-call-site opt-in.
  return (
    <ToastProvider>
      <QueryProviders>{children}</QueryProviders>
    </ToastProvider>
  )
}

/**
 * A mutation cache with a global onError so a mutation that defines no
 * onError of its own still surfaces its failure — silence is no longer a
 * valid default. Call sites that want bespoke copy can still add their own
 * onError; that one runs in addition to this global one.
 */
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
      },
    },
  })
}

function QueryProviders({ children }: { children: React.ReactNode }) {
  const { showToast } = useToast()

  const [queryClient] = useState(() => createAppQueryClient(showToast))

  // OidcSessionProviderWrapper is a no-op when the active provider is not
  // `oidc` — the Firebase path is unchanged at runtime.
  const core = (
    <OidcSessionProviderWrapper>
      <ConfigProvider>
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
