// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { Metadata } from "next"
import { DM_Sans, Fraunces } from "next/font/google"
import "./globals.css"
import { Providers } from "@/components/providers"
import { DEFAULT_THEME } from "@/lib/theme"

// Static no-FOUC bootstrap: applies the saved theme (localStorage key
// "pablo-theme" — must match THEME_STORAGE_KEY) before first paint, falling
// back to the server-rendered data-default-theme attribute. Kept free of any
// interpolated values so it can't construct code from a tainted source.
const THEME_INIT_SCRIPT =
  '(function(){var d=document.documentElement;var f=d.getAttribute("data-default-theme")||"warm-paper";try{d.dataset.theme=localStorage.getItem("pablo-theme")||f;}catch(e){d.dataset.theme=f;}})();'

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm-sans",
})

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
})

export const metadata: Metadata = {
  title: "Pablo",
  description: "HIPAA-compliant therapy session management platform",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" data-default-theme={DEFAULT_THEME} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className={`${dmSans.variable} ${fraunces.variable} font-sans`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
