import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ["127.0.0.1"],
  // Suppress X-Powered-By: Next.js — version disclosure aids fingerprinting.
  poweredByHeader: false,
  // Standalone output for Docker/Cloud Run deployments
  // This creates a minimal production build with only necessary files
  output: "standalone",
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "lh3.googleusercontent.com",
        pathname: "/**",
      },
    ],
  },
  async rewrites() {
    // Serve the Firebase auth helper (/__/auth/handler, /__/auth/iframe,
    // /__/firebase/init.json) from this app's own domain by proxying to the
    // Firebase-hosted helper. This lets us set `authDomain` to our own domain
    // so the Google OAuth consent reads "continue to <our domain>" instead of
    // <project>.firebaseapp.com. Origin is taken from FIREBASE_AUTH_HELPER_ORIGIN,
    // else derived from FIREBASE_PROJECT_ID; if neither is set the proxy is
    // skipped (e.g. local dev with no Firebase project).
    const projectId =
      process.env.FIREBASE_PROJECT_ID || process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
    const helperOrigin =
      process.env.FIREBASE_AUTH_HELPER_ORIGIN ||
      (projectId ? `https://${projectId}.firebaseapp.com` : undefined);

    return [
      {
        // Firebase auth action emails link to /__/auth/action
        // but Next.js treats _-prefixed dirs as private, so rewrite to /auth/action.
        // Must precede the proxy rule below — this one is handled locally.
        source: "/__/auth/action",
        destination: "/auth/action",
      },
      ...(helperOrigin
        ? [
            {
              source: "/__/auth/:path*",
              destination: `${helperOrigin}/__/auth/:path*`,
            },
            {
              source: "/__/firebase/:path*",
              destination: `${helperOrigin}/__/firebase/:path*`,
            },
          ]
        : []),
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Cross-Origin-Opener-Policy",
            value: "same-origin-allow-popups",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
