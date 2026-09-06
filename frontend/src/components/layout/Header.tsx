// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import { signOutAndClear } from "@/lib/auth/signOutAndClear"
import Image from "next/image"
import { UserCircle, LogOut } from "lucide-react"
import { ThemeMenu } from "@/components/theme/ThemeMenu"
import { userMenuItems } from "./userMenuExtensions"

interface HeaderProps {
  user: {
    name?: string | null
    email?: string | null
    image?: string | null
  }
}

export function Header({ user }: HeaderProps) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [isMenuOpen, setIsMenuOpen] = useState(false)

  const handleSignOut = async () => {
    await signOutAndClear(queryClient, router, "/login")
  }

  return (
    <header className="h-16 bg-card border-b border-neutral-200">
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
            {/* Name only. The signed-in address used to sit under it on every
                screen, which is a detail the account holder already knows and
                everyone looking over their shoulder does not. Clinicians share
                this window — with a patient on a call, with a supervisor, in a
                recorded session — and a work address is the one identifier worth
                not leaving on screen by default. It moves into the menu below,
                one click away, which is where it is actually wanted. */}
            <div className="text-left">
              <div className="text-sm font-medium text-neutral-900">
                {user.name || "User"}
              </div>
            </div>
          </button>

          {isMenuOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setIsMenuOpen(false)}
              />
              <div className="absolute right-0 mt-2 w-52 bg-card rounded-lg shadow-lg border border-neutral-200 py-1 z-20 animate-in fade-in slide-in-from-top-2 duration-200">
                {user.email ? (
                  <div className="border-b border-neutral-200 px-4 pb-2 pt-1.5">
                    <div className="truncate text-xs text-neutral-500" title={user.email}>
                      {user.email}
                    </div>
                  </div>
                ) : null}
                <ThemeMenu />
                {/* Slot items sit between the theme control and Sign out, so
                    Sign out stays the last thing in the menu. */}
                {userMenuItems.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setIsMenuOpen(false)}
                    className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors duration-150"
                  >
                    <item.icon className="h-4 w-4" />
                    {item.name}
                  </Link>
                ))}
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
