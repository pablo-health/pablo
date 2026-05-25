// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { Metadata } from "next"
import { DM_Sans, Fraunces } from "next/font/google"
import "./globals.css"
import { Providers } from "@/components/providers"
import { DEFAULT_THEME, THEME_STORAGE_KEY } from "@/lib/theme"

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
  const themeInit = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
    THEME_STORAGE_KEY,
  )});document.documentElement.dataset.theme=t||${JSON.stringify(
    DEFAULT_THEME,
  )};}catch(e){document.documentElement.dataset.theme=${JSON.stringify(
    DEFAULT_THEME,
  )};}})();`

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className={`${dmSans.variable} ${fraunces.variable} font-sans`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
