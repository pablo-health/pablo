// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A claim's service lines with what the payer allowed and paid on each.
 * Before adjudication the money columns read as pending rather than zero:
 * nothing has been decided yet, and a zero would say the payer paid nothing.
 */

"use client"

import { formatCents } from "@/lib/money"
import type { ClaimLine } from "@/types/claims"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatIsoDate } from "./claimPresentation"

interface ClaimLinesTableProps {
  lines: ClaimLine[]
  adjudicated: boolean
}

export function ClaimLinesTable({ lines, adjudicated }: ClaimLinesTableProps) {
  const pending = <span className="text-neutral-400">Pending</span>
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>#</TableHead>
            <TableHead>Date</TableHead>
            <TableHead>Code</TableHead>
            <TableHead>Units</TableHead>
            <TableHead>Charged</TableHead>
            <TableHead>Allowed</TableHead>
            <TableHead>Paid</TableHead>
            <TableHead>Client owes</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lines.map((line) => (
            <TableRow key={line.id} data-testid="claim-line">
              <TableCell>{line.line_number}</TableCell>
              <TableCell>{formatIsoDate(line.service_date)}</TableCell>
              <TableCell>
                {line.cpt}
                {line.modifiers.length > 0 && (
                  <span className="text-neutral-500"> {line.modifiers.join(" ")}</span>
                )}
              </TableCell>
              <TableCell>{line.units}</TableCell>
              <TableCell>{formatCents(line.charge_cents)}</TableCell>
              <TableCell>
                {adjudicated && line.allowed_cents !== null
                  ? formatCents(line.allowed_cents)
                  : pending}
              </TableCell>
              <TableCell>{adjudicated ? formatCents(line.paid_cents) : pending}</TableCell>
              <TableCell>
                {adjudicated && line.patient_resp_cents !== null
                  ? formatCents(line.patient_resp_cents)
                  : pending}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
