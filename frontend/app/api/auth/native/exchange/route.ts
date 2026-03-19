// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = process.env.API_URL || "http://localhost:8000"

export async function POST(request: NextRequest) {
  const body = await request.json()

  const res = await fetch(`${BACKEND_URL}/api/auth/native/exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })

  const data = await res.json()
  return NextResponse.json(data, { status: res.status })
}
