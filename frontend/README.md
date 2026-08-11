# Pablo - Frontend

HIPAA-compliant therapy documentation platform built with Next.js and Firebase Authentication.

## Tech Stack

- **Next.js 16** - React framework with App Router
- **React 19** - Component-based UI
- **TypeScript** - Type safety
- **Tailwind CSS** + **shadcn/ui** - Styling and components
- **Firebase Authentication** (Google Identity Platform) - Sign-in and session management, with edge token verification via `next-firebase-auth-edge`
- **lucide-react** - UI icons

## Getting Started

### Prerequisites

- Node.js 24 installed
- A Firebase / Google Identity Platform project (for authentication)

### Setup

1. **Install dependencies**
   ```bash
   npm install
   ```

2. **Set up environment variables**

   Copy the example file:
   ```bash
   cp .env.example .env.local
   ```

   Then edit `.env.local`. The Firebase client config (embedded in the
   browser bundle) is read from:
   - `NEXT_PUBLIC_FIREBASE_API_KEY`
   - `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`
   - `NEXT_PUBLIC_FIREBASE_PROJECT_ID`
   - `NEXT_PUBLIC_FIREBASE_APP_ID`

   Server-side token verification (`next-firebase-auth-edge`) uses a
   service-account credential and a cookie signing key:
   - `FIREBASE_PROJECT_ID`
   - `FIREBASE_CLIENT_EMAIL`
   - `FIREBASE_PRIVATE_KEY`
   - `AUTH_COOKIE_SIGNATURE_KEY` - generate with `openssl rand -base64 32`

   The backend base URL is set with `API_URL` (defaults to
   `http://localhost:8000`). See `.env.example` for the full list,
   including local dev-mode toggles.

3. **Run the development server**
   ```bash
   npm run dev
   ```

   Open [http://localhost:3000](http://localhost:3000)

## Features

- Firebase authentication with multi-factor enrollment and passkey support
- Protected dashboard routes
- Patient management
- Session management and transcript upload
- AI-generated SOAP notes with review and finalize workflow
- Responsive navigation and HIPAA compliance messaging

## Project Structure

```
frontend/
├── app/
│   ├── (dashboard)/         # Dashboard layout group
│   │   ├── layout.tsx       # Dashboard shell (Sidebar + Header)
│   │   └── dashboard/       # Dashboard pages
│   ├── login/               # Login page
│   ├── layout.tsx           # Root layout
│   └── globals.css          # Global styles
├── src/
│   ├── components/          # UI and feature components
│   └── lib/
│       └── auth/            # Firebase auth package
│           ├── firebase/    # Client SDK, login/MFA screens, server helpers
│           ├── middleware.ts
│           └── provider.ts
└── proxy.ts                 # Route protection
```

## HIPAA Compliance Notes

This application is designed with HIPAA compliance in mind:

- Uses HTTPS in production (configure in deployment)
- Firebase authentication and authorization on every route
- Backend-enforced idle session timeout
- No PHI stored in client-side storage

Compliance is layered with the backend: application-level audit
logging, encryption at rest, and Business Associate Agreements are
handled server-side and in deployment. See `docs/HIPAA_AUDIT_LOGS.md`
and `docs/SELF_HOSTING_HIPAA_GUIDE.md` in the repository root.

## Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm start` - Start production server
- `npm run lint` - Run ESLint
- `npm test` - Run Vitest
