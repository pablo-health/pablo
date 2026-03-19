// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Component, type ReactNode } from "react"

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
}

/**
 * Reusable React Error Boundary for catching rendering errors in child components.
 * Logs errors safely (no PHI) and shows a user-friendly fallback UI.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error): void {
    // Log only the error name — never the message or stack (may contain PHI)
    console.error("ErrorBoundary caught:", error.name)
  }

  handleReset = () => {
    this.setState({ hasError: false })
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }

      return (
        <div className="flex items-center justify-center p-8" role="alert">
          <div className="max-w-md space-y-4 rounded-lg border bg-white p-6 text-center shadow-sm">
            <h2 className="text-lg font-semibold text-neutral-900">
              Something went wrong
            </h2>
            <p className="text-sm text-neutral-600">
              An unexpected error occurred. Please try again.
            </p>
            <button
              onClick={this.handleReset}
              className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors"
            >
              Try again
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
