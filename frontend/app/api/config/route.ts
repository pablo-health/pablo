// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { NextResponse } from 'next/server'

const IS_PRODUCTION = process.env.NODE_ENV === 'production'

export async function GET() {
  // In production, force safe defaults for dev/mock flags
  // to prevent exposing internal configuration to unauthenticated users
  return NextResponse.json({
    apiUrl: process.env.API_URL || 'http://localhost:8000',
    devMode: IS_PRODUCTION ? false : process.env.DEV_MODE === 'true',
    dataMode: IS_PRODUCTION ? 'api' : (process.env.DATA_MODE || 'mock'),
    enableLocalAuth: IS_PRODUCTION ? false : process.env.ENABLE_LOCAL_AUTH === 'true',
    multiTenancyEnabled: process.env.MULTI_TENANCY_ENABLED === 'true',
    firebaseProjectId: process.env.FIREBASE_PROJECT_ID || '',
    firebaseApiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY || '',
    firebaseAuthDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || '',
    firebaseAppId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID || '',
    ratingFeedbackRequiredBelow: parseInt(process.env.RATING_FEEDBACK_REQUIRED_BELOW || '5', 10),
    showVerificationBadges: process.env.SHOW_VERIFICATION_BADGES === 'true',
  })
}
