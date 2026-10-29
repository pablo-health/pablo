// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo } from "react"
import {
  useClearFloatingBlockException,
  useFloatingBlocksForDates,
  useSetFloatingBlockException,
} from "@/hooks/useFloatingBlocks"
import { useToast } from "@/components/ui/Toast"
import type { FloatingBlock } from "@/types/availability"
import type { FloatingBlockActions } from "./FloatingBlockBand"
import { addDays, format, visibleRange, type EditorialView } from "./dateUtils"

/** The visible days of the day and week views, as "YYYY-MM-DD". */
function visibleDates(view: EditorialView, anchor: Date): string[] {
  if (view === "month") return []
  const { start } = visibleRange(view, anchor)
  const count = view === "day" ? 1 : 7
  return Array.from({ length: count }, (_, i) => format(addDays(start, i), "yyyy-MM-dd"))
}

/**
 * The floating blocks on the visible days, and what the bands can do with
 * them. The month view draws no bands, so it asks for none.
 */
export function useFloatingBlockBands(
  view: EditorialView,
  anchor: Date,
  enabled: boolean,
): { blocks: FloatingBlock[]; actions: FloatingBlockActions } {
  const dates = useMemo(() => visibleDates(view, anchor), [view, anchor])
  const blocks = useFloatingBlocksForDates(dates, enabled)
  const { mutate: setException, isPending: setting } = useSetFloatingBlockException()
  const { mutate: clearException, isPending: clearing } = useClearFloatingBlockException()
  const { showToast } = useToast()

  const pending = setting || clearing
  const actions = useMemo<FloatingBlockActions>(() => {
    const onError = () => showToast("Couldn't change that. Please try again.", "error")
    return {
      pending,
      onSkip: (block) =>
        setException(
          { ruleId: block.rule_id, date: block.date, exception: { skip: true } },
          { onError },
        ),
      onMoveTo: (block, start) =>
        setException(
          { ruleId: block.rule_id, date: block.date, exception: { start } },
          { onError },
        ),
      onPutBack: (block) =>
        clearException({ ruleId: block.rule_id, date: block.date }, { onError }),
    }
  }, [pending, setException, clearException, showToast])

  return { blocks, actions }
}
