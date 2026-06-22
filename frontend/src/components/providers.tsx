// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { useEffect, useState } from "react"
import { ConfigProvider } from "@/lib/config-provider"
import { AuthProvider } from "@/lib/auth-context"
import { ToastProvider } from "@/components/ui/Toast"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { installGlobalErrorReporter } from "@/lib/feErrorReporter"
import { OidcSessionProviderWrapper } from "@/lib/auth/oidc/SessionProviderWrapper"
import { outerProviderWrappers } from "./providers.extensions"

export function Providers({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    installGlobalErrorReporter()
  }, [])

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000,
            refetchOnWindowFocus: false,
          },
        },
      })
  )

  // OidcSessionProviderWrapper is a no-op when the active provider is not
  // `oidc` — the Firebase path is unchanged at runtime.
  const core = (
    <OidcSessionProviderWrapper>
      <ConfigProvider>
        <AuthProvider>
          <ThemeProvider>
            <ToastProvider>
              {children}
              <ReactQueryDevtools initialIsOpen={false} />
            </ToastProvider>
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
