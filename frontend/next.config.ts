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
    // /__/firebase/init.json) from this app's own domain, so `authDomain`
    // can be set to our own domain and the Google OAuth consent reads
    // "continue to <our domain>" instead of <project>.firebaseapp.com.
    //
    // These rewrites only route the reserved paths to an internal route
    // handler (app/fbauth-proxy) — they carry NO environment lookup. The
    // proxy *target* (the Firebase-hosted helper) is resolved at REQUEST
    // time inside that handler, because rewrites() is evaluated at build
    // time, when FIREBASE_PROJECT_ID is unset, and because the same image
    // is promoted dev->prod (so a build-time target would be wrong for one
    // of them). Underscore-prefixed dirs are private in Next, so we can't
    // serve /__/* directly; the handler lives under /fbauth-proxy/* instead.
    return [
      {
        // Firebase auth action emails link to /__/auth/action; that one is
        // a real local page. Must precede the catch-all proxy rule below.
        source: "/__/auth/action",
        destination: "/auth/action",
      },
      {
        source: "/__/auth/:path*",
        destination: "/fbauth-proxy/auth/:path*",
      },
      {
        source: "/__/firebase/:path*",
        destination: "/fbauth-proxy/firebase/:path*",
      },
    ];
  },
  async headers() {
    return [
      {
        // Apply COOP to every page EXCEPT the Firebase auth helper paths
        // (/__/auth/*, /__/firebase/*, and their /fbauth-proxy/* targets).
        // signInWithPopup needs the *opener* (our app pages) to send
        // same-origin-allow-popups so it can retain the popup handle, but the
        // helper page that loads inside the popup must be served bare — exactly
        // as it is from <project>.firebaseapp.com. Stamping COOP on the
        // returning handler document forces a browsing-context-group swap that
        // severs the opener's handle, so the SDK delivers the credential but
        // can never close the popup (it hangs blank).
        source: "/((?!__/|fbauth-proxy/).*)",
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
