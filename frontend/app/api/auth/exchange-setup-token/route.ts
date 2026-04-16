// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { NextRequest, NextResponse } from "next/server"

const API_URL = process.env.API_URL || "http://localhost:8000"

export async function POST(req: NextRequest) {
  const { token } = await req.json()

  const res = await fetch(`${API_URL}/api/auth/exchange-setup-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  })

  const data = await res.json()
  return NextResponse.json(data)
}
