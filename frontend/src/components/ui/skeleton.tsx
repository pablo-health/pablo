// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { cn } from "@/lib/utils"

function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-neutral-200", className)}
      {...props}
    />
  )
}

export { Skeleton }
