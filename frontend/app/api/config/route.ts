// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { NextResponse } from 'next/server'
import { IS_DEV_MODE } from '@/lib/devMode'

const IS_PRODUCTION = process.env.NODE_ENV === 'production'

export async function GET() {
  // In production, force safe defaults for dev/mock flags
  // to prevent exposing internal configuration to unauthenticated users
  return NextResponse.json({
    apiUrl: process.env.API_URL || 'http://localhost:8000',
    devMode: IS_DEV_MODE,
    dataMode: IS_PRODUCTION ? 'api' : (process.env.DATA_MODE || 'api'),
    enableLocalAuth: IS_PRODUCTION ? false : process.env.ENABLE_LOCAL_AUTH === 'true',
    pabloEdition: process.env.PABLO_EDITION || 'core',
    firebaseProjectId: process.env.FIREBASE_PROJECT_ID || '',
    firebaseApiKey: process.env.FIREBASE_API_KEY || process.env.NEXT_PUBLIC_FIREBASE_API_KEY || '',
    firebaseAuthDomain: process.env.FIREBASE_AUTH_DOMAIN || process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || '',
    firebaseAppId: process.env.FIREBASE_APP_ID || process.env.NEXT_PUBLIC_FIREBASE_APP_ID || '',
    ratingFeedbackRequiredBelow: parseInt(process.env.RATING_FEEDBACK_REQUIRED_BELOW || '5', 10),
    showVerificationBadges: process.env.SHOW_VERIFICATION_BADGES === 'true',
    introVideoUrl: process.env.INTRO_VIDEO_URL || '',
    // Runtime toggle (no client rebuild) for the WebAuthn passkey UI, dark
    // until the egm.4 cutover. Set PASSKEYS_ENABLED=true on the container.
    passkeysEnabled: process.env.PASSKEYS_ENABLED === 'true',
    // Where the app sends someone whose subscription has ended when they
    // choose to start again. Deployment-specific (a hosted deployment
    // points at its own reactivation/checkout page); empty means the UI
    // falls back to whatever default it ships with.
    resubscribeUrl: process.env.RESUBSCRIBE_URL || '',
    // Runtime toggle for the "Booking links" Settings section, mirroring the
    // backend's public_booking_enabled setting. Same env var on both
    // containers so a deployment flips one value to turn on client
    // self-booking end to end.
    publicBookingEnabled: process.env.PUBLIC_BOOKING_ENABLED === 'true',
    // Runtime toggle for the "Google Calendar" Settings section. Read from
    // container env rather than baked in at build time, so one image can be
    // dark in one deployment and live in another. Turn it on once the
    // backend has GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET
    // set — without those the connect flow has nothing to send Google.
    googleCalendarEnabled: process.env.GOOGLE_CALENDAR_ENABLED === 'true',
  })
}
