// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A failure that something else has already explained (``handled_externally``)
 * must not be rendered as an alert, or with any of the panel's own copy for a
 * status. Every other code keeps its existing copy.
 */
import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"

import { ChatErrorNotice } from "../ChatErrorNotice"

describe("ChatErrorNotice", () => {
  it("shows a handled failure's own line as a note, not an alert", () => {
    render(<ChatErrorNotice code="handled_externally" serverMessage="Explained elsewhere" />)

    expect(screen.getByRole("status")).toHaveTextContent("Explained elsewhere")
    expect(screen.queryByRole("alert")).toBeNull()
    expect(screen.queryByText(/not signed in/i)).toBeNull()
  })

  it("renders nothing for a handled failure with no line of its own", () => {
    const { container } = render(<ChatErrorNotice code="handled_externally" serverMessage="" />)

    expect(container).toBeEmptyDOMElement()
  })

  it("keeps the sign-in copy for auth_denied", () => {
    render(<ChatErrorNotice code="auth_denied" serverMessage="raw body" />)

    expect(screen.getByRole("alert")).toHaveTextContent("You're not signed in to use chat.")
  })
})
