// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { formatFileSize } from "@/lib/utils/fileValidation"

/** "3 files, 1.2 KB" */
export function describeFiles(count: number, bytes: number): string {
  return `${count} ${count === 1 ? "file" : "files"}, ${formatFileSize(bytes)}`
}

/** "Sep 1, 2026, 3:04 PM" */
export function formatWhen(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}
