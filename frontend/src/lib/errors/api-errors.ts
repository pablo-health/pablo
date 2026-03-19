// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * API Error Handling Utilities
 *
 * Provides error code constants and helper functions for handling API errors.
 */

import { ApiError } from "../api/client"

/**
 * Known API error codes from backend
 */
export const ErrorCodes = {
  // BAA-related errors
  BAA_NOT_ACCEPTED: "BAA_NOT_ACCEPTED",

  // Authentication errors
  UNAUTHORIZED: "UNAUTHORIZED",
  FORBIDDEN: "FORBIDDEN",

  // Validation errors
  BAD_REQUEST: "BAD_REQUEST",
  VALIDATION_ERROR: "VALIDATION_ERROR",

  // Resource errors
  NOT_FOUND: "NOT_FOUND",

  // Server errors
  INTERNAL_SERVER_ERROR: "INTERNAL_SERVER_ERROR",
  UNKNOWN_ERROR: "UNKNOWN_ERROR",

  // Network errors
  NETWORK_ERROR: "NETWORK_ERROR",
} as const

export type ErrorCode = (typeof ErrorCodes)[keyof typeof ErrorCodes]

/**
 * Check if an error is an ApiError
 */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

/**
 * Check if an error has a specific error code
 */
export function hasErrorCode(error: unknown, code: ErrorCode): boolean {
  return isApiError(error) && error.code === code
}

/**
 * Check if error is a BAA not accepted error
 */
export function isBAANotAcceptedError(error: unknown): boolean {
  return hasErrorCode(error, ErrorCodes.BAA_NOT_ACCEPTED)
}

/**
 * Check if error is a network error
 */
export function isNetworkError(error: unknown): boolean {
  return hasErrorCode(error, ErrorCodes.NETWORK_ERROR)
}

/**
 * Get user-friendly error message
 */
export function getUserErrorMessage(error: unknown): string {
  if (!isApiError(error)) {
    return "An unexpected error occurred. Please try again."
  }

  // Specific error messages based on code
  switch (error.code) {
    case ErrorCodes.BAA_NOT_ACCEPTED:
      return "You must accept the Business Associate Agreement to continue."

    case ErrorCodes.UNAUTHORIZED:
      return "You must be logged in to access this resource."

    case ErrorCodes.FORBIDDEN:
      return "You don't have permission to access this resource."

    case ErrorCodes.NOT_FOUND:
      return "The requested resource was not found."

    case ErrorCodes.NETWORK_ERROR:
      return "Network error. Please check your connection and try again."

    case ErrorCodes.BAD_REQUEST:
    case ErrorCodes.VALIDATION_ERROR:
      // Use the specific message from the backend
      return error.message

    case ErrorCodes.INTERNAL_SERVER_ERROR:
      return "A server error occurred. Please try again later."

    default:
      return error.message || "An error occurred. Please try again."
  }
}

/**
 * Format validation errors from backend for form display
 *
 * @param error - API error
 * @returns Object mapping field names to error messages
 */
export function getValidationErrors(
  error: unknown
): Record<string, string> | null {
  if (!isApiError(error)) {
    return null
  }

  // Check if error has validation details
  if (error.details && typeof error.details === "object") {
    const errors: Record<string, string> = {}

    // Convert backend field names to camelCase for form
    for (const [field, message] of Object.entries(error.details)) {
      if (typeof message === "string") {
        // Convert snake_case to camelCase
        const camelField = field.replace(/_([a-z])/g, (_, letter) =>
          letter.toUpperCase()
        )
        errors[camelField] = message
      }
    }

    return Object.keys(errors).length > 0 ? errors : null
  }

  return null
}
