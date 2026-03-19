// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Toast Notification Component
 *
 * Displays temporary notification messages for API errors and other transient events.
 * Auto-dismisses after 5 seconds with manual close option.
 */

import { X } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

export type ToastType = "error" | "success" | "info" | "warning"

export interface ToastProps {
  message: string
  type?: ToastType
  duration?: number
  onClose: () => void
}

/**
 * Toast notification component
 *
 * @param message - The message to display
 * @param type - Type of toast (error, success, info, warning). Default: "error"
 * @param duration - Auto-dismiss duration in milliseconds. Default: 5000 (5 seconds)
 * @param onClose - Callback when toast is closed
 */
export function Toast({
  message,
  type = "error",
  duration = 5000,
  onClose,
}: ToastProps) {
  const [isVisible, setIsVisible] = useState(false)

  const handleClose = useCallback(() => {
    setIsVisible(false)
    // Wait for slide-out animation before removing
    setTimeout(() => {
      onClose()
    }, 300)
  }, [onClose])

  useEffect(() => {
    // Trigger slide-in animation after a brief delay to ensure CSS transition works
    const animationTimer = setTimeout(() => {
      setIsVisible(true)
    }, 10)

    // Auto-dismiss after duration
    const dismissTimer = setTimeout(() => {
      handleClose()
    }, duration)

    return () => {
      clearTimeout(animationTimer)
      clearTimeout(dismissTimer)
    }
  }, [duration, handleClose])

  // Color scheme based on type
  const colors = {
    error: "bg-red-50 border-red-200 text-red-800",
    success: "bg-green-50 border-green-200 text-green-800",
    info: "bg-blue-50 border-blue-200 text-blue-800",
    warning: "bg-yellow-50 border-yellow-200 text-yellow-800",
  }

  const iconColors = {
    error: "text-red-600",
    success: "text-green-600",
    info: "text-blue-600",
    warning: "text-yellow-600",
  }

  return (
    <div
      className={`
        fixed top-4 right-4 z-50
        max-w-md px-4 py-3 rounded-lg border-2 shadow-lg
        flex items-start gap-3
        transition-all duration-300 ease-in-out
        ${isVisible ? "translate-x-0 opacity-100" : "translate-x-full opacity-0"}
        ${colors[type]}
      `}
      role="alert"
    >
      <p className="flex-1 text-sm font-medium">{message}</p>
      <button
        onClick={handleClose}
        className={`
          flex-shrink-0 hover:opacity-70 transition-opacity
          ${iconColors[type]}
        `}
        aria-label="Close notification"
      >
        <X className="w-5 h-5" />
      </button>
    </div>
  )
}

/**
 * Toast Container for managing multiple toasts
 */
interface ToastMessage {
  id: string
  message: string
  type: ToastType
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<ToastMessage[]>([])

  const addToast = (message: string, type: ToastType = "error") => {
    const id = Date.now().toString()
    setToasts((prev) => [...prev, { id, message, type }])
  }

  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id))
  }

  // Expose addToast function globally for easy access
  useEffect(() => {
    ;(window as unknown as { showToast: typeof addToast }).showToast = addToast
  }, [])

  return (
    <>
      {toasts.map((toast, index) => (
        <div
          key={toast.id}
          style={{ top: `${16 + index * 80}px` }}
          className="fixed right-4 z-50"
        >
          <Toast
            message={toast.message}
            type={toast.type}
            onClose={() => removeToast(toast.id)}
          />
        </div>
      ))}
    </>
  )
}

/**
 * Helper function to show a toast notification
 *
 * Usage:
 * import { showToast } from '@/components/ui/Toast'
 * showToast('Error message', 'error')
 */
export function showToast(message: string, type: ToastType = "error") {
  const event = new CustomEvent("showToast", { detail: { message, type } })
  window.dispatchEvent(event)
}
