// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText } from "lucide-react"
import { useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import type { IntakeStarter } from "@/types/intakeDocuments"
import {
  ADD_DOCUMENT_TO_PACKET,
  BUILT_IN_DOCUMENTS,
  NO_DOCUMENTS_TO_ADD,
  STARTER_ADDS_DOCUMENT,
  WRITE_NEW_DOCUMENT,
  YOUR_DOCUMENTS,
} from "./intakeCopy"
import type { PublishedDocument } from "./ItemConfigForm"

interface AddDocumentPickerProps {
  /** The practice's published documents. */
  documents: PublishedDocument[]
  /** Built-in starters that bring a document. */
  builtIns: IntakeStarter[]
  onPickDocument: (document: PublishedDocument) => void
  onPickBuiltIn: (key: string) => void
  /** Writing a new document here; called back with it once it is published. */
  renderNewDocument?: (choose: (documentKey: string, title?: string) => void) => ReactNode
  busy?: boolean
}

/**
 * "Add a document", beside "Add question" on a draft packet.
 *
 * One list: the practice's own documents, then the built-in ones, then
 * writing a new one. Whichever is picked goes on the packet as a document to
 * sign; which wording gets signed is set on that item afterwards.
 */
export function AddDocumentPicker({
  documents,
  builtIns,
  onPickDocument,
  onPickBuiltIn,
  renderNewDocument,
  busy,
}: AddDocumentPickerProps) {
  const [open, setOpen] = useState(false)
  const [writing, setWriting] = useState(false)

  function close() {
    setOpen(false)
    setWriting(false)
  }

  return (
    <div className="space-y-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => (open ? close() : setOpen(true))}
        aria-expanded={open}
      >
        <FileText className="mr-1 h-4 w-4" aria-hidden="true" />
        {ADD_DOCUMENT_TO_PACKET}
      </Button>

      {open && (
        <div className="space-y-3 rounded-xl border border-border p-3">
          <div className="space-y-1">
            <p className="text-[12.5px] font-semibold text-foreground">{YOUR_DOCUMENTS}</p>
            {documents.length === 0 ? (
              <p className="text-[12.5px] text-muted-foreground">{NO_DOCUMENTS_TO_ADD}</p>
            ) : (
              <ul aria-label={YOUR_DOCUMENTS} className="space-y-1">
                {documents.map((document) => (
                  <li key={document.document_key}>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        close()
                        onPickDocument(document)
                      }}
                    >
                      {document.title}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {builtIns.length > 0 && (
            <div className="space-y-1">
              <p className="text-[12.5px] font-semibold text-foreground">{BUILT_IN_DOCUMENTS}</p>
              <p className="text-[12px] text-muted-foreground">{STARTER_ADDS_DOCUMENT}</p>
              <ul aria-label={BUILT_IN_DOCUMENTS} className="space-y-1">
                {builtIns.map((starter) => (
                  <li key={starter.key}>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        close()
                        onPickBuiltIn(starter.key)
                      }}
                    >
                      {starter.title}
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {renderNewDocument &&
            (writing ? (
              renderNewDocument((documentKey, title) => {
                close()
                onPickDocument({ document_key: documentKey, title: title ?? "" })
              })
            ) : (
              <Button type="button" variant="outline" size="sm" onClick={() => setWriting(true)}>
                {WRITE_NEW_DOCUMENT}
              </Button>
            ))}
        </div>
      )}
    </div>
  )
}
