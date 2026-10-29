// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"

/** Longest clip offered; the server allows a little over (MAX_DICTATION_SECONDS). */
export const MAX_DICTATION_SECONDS = 600

export type RecorderState = "idle" | "starting" | "recording" | "recorded"
export type RecorderProblem = "denied" | "no-microphone" | "unsupported" | "failed"

export interface DictationClip {
  blob: Blob
  seconds: number
}

/** The first container this browser records in that transcription accepts. */
function preferredMimeType(): string | undefined {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
  return candidates.find((type) => MediaRecorder.isTypeSupported?.(type))
}

function problemFor(error: unknown): RecorderProblem {
  const name = error instanceof DOMException || error instanceof Error ? error.name : ""
  if (name === "NotAllowedError" || name === "SecurityError") return "denied"
  if (name === "NotFoundError" || name === "OverconstrainedError") return "no-microphone"
  return "failed"
}

/**
 * Record one clip from the microphone with the browser's MediaRecorder.
 *
 * The microphone is asked for only when recording starts, and released as
 * soon as it stops or the component goes away. Recording stops by itself
 * at {@link MAX_DICTATION_SECONDS}.
 */
export function useDictationRecorder() {
  const [state, setState] = useState<RecorderState>("idle")
  const [problem, setProblem] = useState<RecorderProblem | null>(null)
  const [seconds, setSeconds] = useState(0)
  const [clip, setClip] = useState<DictationClip | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startedAtRef = useRef(0)
  const keepRef = useRef(true)

  const release = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
  }, [])

  useEffect(() => {
    return () => {
      keepRef.current = false
      if (recorderRef.current?.state === "recording") recorderRef.current.stop()
      release()
    }
  }, [release])

  const stop = useCallback(() => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop()
  }, [])

  const start = useCallback(async () => {
    setProblem(null)
    setClip(null)
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setProblem("unsupported")
      return
    }
    setState("starting")
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (error) {
      setProblem(problemFor(error))
      setState("idle")
      return
    }
    streamRef.current = stream
    const mimeType = preferredMimeType()
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    const chunks: Blob[] = []
    keepRef.current = true
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    recorder.onstop = () => {
      const elapsed = (Date.now() - startedAtRef.current) / 1000
      release()
      if (!keepRef.current) {
        setState("idle")
        return
      }
      const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" })
      setClip({ blob, seconds: Math.min(elapsed, MAX_DICTATION_SECONDS) })
      setState("recorded")
    }
    recorderRef.current = recorder
    startedAtRef.current = Date.now()
    setSeconds(0)
    recorder.start()
    setState("recording")
    timerRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startedAtRef.current) / 1000)
      setSeconds(elapsed)
      if (elapsed >= MAX_DICTATION_SECONDS) recorder.stop()
    }, 1000)
  }, [release])

  /** Throw the clip away, mid-recording or after. */
  const discard = useCallback(() => {
    keepRef.current = false
    if (recorderRef.current?.state === "recording") recorderRef.current.stop()
    else release()
    setClip(null)
    setSeconds(0)
    setState("idle")
  }, [release])

  return { state, problem, seconds, clip, start, stop, discard }
}
