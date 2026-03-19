// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

'use client'

import React, { createContext, useContext, ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { setApiUrl } from './api/client'

export interface RuntimeConfig {
  apiUrl: string
  devMode: boolean
  dataMode: string
  enableLocalAuth: boolean
  multiTenancyEnabled: boolean
  firebaseProjectId: string
  firebaseApiKey: string
  firebaseAuthDomain: string
  firebaseAppId: string
  ratingFeedbackRequiredBelow: number
  showVerificationBadges: boolean
}

interface ConfigContextValue {
  config: RuntimeConfig | undefined
  isLoading: boolean
  error: Error | null
}

const ConfigContext = createContext<ConfigContextValue | undefined>(undefined)

async function fetchConfig(): Promise<RuntimeConfig> {
  const response = await fetch('/api/config')
  if (!response.ok) {
    throw new Error(`Failed to fetch config: ${response.statusText}`)
  }
  return response.json()
}

export function ConfigProvider({ children }: { children: ReactNode }) {
  const { data: config, isLoading, error } = useQuery<RuntimeConfig, Error>({
    queryKey: ['runtime-config'],
    queryFn: fetchConfig,
    staleTime: Infinity,
    retry: 3,
    retryDelay: 1000,
  })

  // Set API URL synchronously before children render.
  // useEffect would fire AFTER children mount, causing a race condition
  // where React Query fetches hit localhost instead of the backend.
  if (config?.apiUrl) {
    setApiUrl(config.apiUrl)
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-red-600 mb-4">Configuration Error</h1>
          <p className="text-gray-700 mb-4">{error.message}</p>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4 mx-auto"></div>
          <p className="text-gray-700">Loading configuration...</p>
        </div>
      </div>
    )
  }

  return (
    <ConfigContext.Provider value={{ config, isLoading, error }}>
      {children}
    </ConfigContext.Provider>
  )
}

export function useConfig(): RuntimeConfig {
  const context = useContext(ConfigContext)
  if (!context) {
    throw new Error('useConfig must be used within ConfigProvider')
  }
  if (!context.config) {
    throw new Error('Config not loaded yet')
  }
  return context.config
}
