// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Hand a downloaded file to the browser to save.
 *
 * The export routes answer with the document itself rather than a link to
 * one, so there is nothing to open in a tab — the blob is turned into a URL
 * that lives exactly as long as the click.
 */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
