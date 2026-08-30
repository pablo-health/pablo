// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

interface SetupAsideProps {
  img: string
  caption: string
}

/** The framed illustration beside a wizard step, with an italic caption
 * underneath. Hidden below the `md` breakpoint, where there's no room
 * for it beside the step panel. */
export function SetupAside({ img, caption }: SetupAsideProps) {
  return (
    <div className="hidden flex-col items-center gap-3 md:flex">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={img}
        alt=""
        className="h-[158px] w-[190px] rounded-2xl object-cover"
        style={{
          border: "3px solid var(--brand-panel-accent)",
          boxShadow: "var(--shadow-aside, 0 12px 32px rgba(0,0,0,0.18))",
          background: "var(--card)",
        }}
      />
      <p className="text-center font-display text-sm italic text-muted-foreground">{caption}</p>
    </div>
  )
}
