# Pablo - Frontend

HIPAA-compliant therapy session management platform built with Next.js 15 and NextAuth.

## Tech Stack

- **Next.js 15.4.10** - React framework with App Router
- **NextAuth v5** - Authentication with Google OAuth
- **TypeScript** - Type safety
- **Tailwind CSS** - Styling
- **Heroicons** - UI icons

## Getting Started

### Prerequisites

- Node.js 18+ installed
- Google OAuth credentials (Client ID and Secret)

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

   Then edit `.env.local` and add:
   - `AUTH_SECRET` - Generate with: `openssl rand -base64 32`
   - `NEXTAUTH_URL` - Your app URL (http://localhost:3000 for development)
   - `GOOGLE_CLIENT_ID` - From Google Cloud Console
   - `GOOGLE_CLIENT_SECRET` - From Google Cloud Console

3. **Configure Google OAuth**

   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing
   - Enable Google+ API
   - Go to "Credentials" → "Create Credentials" → "OAuth 2.0 Client ID"
   - Add authorized redirect URI: `http://localhost:3000/api/auth/callback/google`

4. **Run the development server**
   ```bash
   npm run dev
   ```

   Open [http://localhost:3000](http://localhost:3000)

## Features

- ✅ Google OAuth authentication
- ✅ Protected dashboard routes
- ✅ Professional UI with Tailwind CSS
- ✅ Responsive sidebar navigation
- ✅ User menu with sign out
- ✅ HIPAA compliance messaging
- 🚧 Patient management (coming soon)
- 🚧 Session management (coming soon)

## Project Structure

```
frontend/
├── app/
│   ├── (dashboard)/         # Dashboard layout group
│   │   ├── layout.tsx       # Dashboard shell (Sidebar + Header)
│   │   └── dashboard/       # Dashboard pages
│   ├── api/auth/            # NextAuth API routes
│   ├── login/               # Login page
│   ├── layout.tsx           # Root layout
│   └── globals.css          # Global styles
├── src/
│   ├── components/
│   │   └── layout/          # Layout components (Sidebar, Header)
│   └── lib/
│       └── auth.ts          # NextAuth configuration
└── middleware.ts            # Route protection
```

## HIPAA Compliance Notes

This application is designed with HIPAA compliance in mind:

- Uses HTTPS in production (configure in deployment)
- Implements authentication and authorization
- Session-based access control
- No PHI stored in client-side storage
- Secure OAuth flow for authentication

**Important**: Additional HIPAA requirements must be implemented:
- Audit logging (see backend tasks)
- Encryption at rest (database level)
- Business Associate Agreements with third parties
- Regular security assessments

## Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm start` - Start production server
- `npm run lint` - Run ESLint

## Next Steps

See the project's issue tracker for upcoming features:
- Patient management UI
- Session management UI
- HIPAA audit logging integration
- Data retention controls
