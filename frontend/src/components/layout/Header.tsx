// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { signOut } from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import Image from "next/image"
import { UserCircle, LogOut } from "lucide-react"

interface HeaderProps {
  user: {
    name?: string | null
    email?: string | null
    image?: string | null
  }
}

export function Header({ user }: HeaderProps) {
  const router = useRouter()
  const [isMenuOpen, setIsMenuOpen] = useState(false)

  const handleSignOut = async () => {
    try {
      await signOut(getFirebaseAuth())
    } catch {
      // Firebase not initialized (dev mode) — just redirect
    }
    await fetch("/api/logout")
    router.push("/login")
  }

  return (
    <header className="h-16 bg-white border-b border-neutral-200">
      <div className="h-full px-6 flex items-center justify-end">
        <div className="relative">
          <button
            onClick={() => setIsMenuOpen(!isMenuOpen)}
            aria-label="Open user menu"
            aria-haspopup="menu"
            aria-expanded={isMenuOpen}
            className="flex items-center gap-3 hover:bg-neutral-50 rounded-lg px-3 py-2 transition-all duration-200 hover:shadow-sm"
          >
            {user.image ? (
              <Image
                src={user.image}
                alt={user.name || "User"}
                width={32}
                height={32}
                className="rounded-full ring-2 ring-neutral-200"
              />
            ) : (
              <UserCircle className="h-8 w-8 text-neutral-400" />
            )}
            <div className="text-left">
              <div className="text-sm font-medium text-neutral-900">
                {user.name || "User"}
              </div>
              <div className="text-xs text-neutral-500">{user.email}</div>
            </div>
          </button>

          {isMenuOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setIsMenuOpen(false)}
              />
              <div className="absolute right-0 mt-2 w-48 bg-white rounded-lg shadow-lg border border-neutral-200 py-1 z-20 animate-in fade-in slide-in-from-top-2 duration-200">
                <button
                  onClick={handleSignOut}
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors duration-150"
                >
                  <LogOut className="h-4 w-4" />
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
