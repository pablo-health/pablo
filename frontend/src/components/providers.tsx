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

  return (
    <QueryClientProvider client={queryClient}>
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
    </QueryClientProvider>
  )
}
