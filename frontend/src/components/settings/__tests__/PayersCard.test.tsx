// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PayersCard tests — the payer list and its deadlines in Settings.
 *
 * What matters here: the three deadlines are editable per payer with the
 * helper text that explains where the numbers come from, and an edit sends
 * only the field that changed. An open payer also shows where the practice
 * stands with it — each enrollment request and what the payer is waiting
 * on — with an "Enroll with payer" button that files the missing ones, and
 * the switches saying which of those Pablo should be asking for at all.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { DEADLINE_HELP, ENROLLMENT_HELP, REMITTANCE_WARNING, PayersCard } from "../PayersCard"
import type {
  EnrollmentTaskListResponse,
  PayerEnrollmentListResponse,
  PayerResponse,
} from "@/types/coverage"

const mockUsePayers = vi.fn()
const mockUpdate = vi.fn()
const mockCreate = vi.fn()
const mockUseEnrollments = vi.fn()
const mockRequestEnrollments = vi.fn()
const mockUseTasks = vi.fn()
const mockAnswerTask = vi.fn()
const mockRefreshEnrollments = vi.fn()
const mockUseRefreshEnrollments = vi.fn()
const mockUseDirectory = vi.fn()

vi.mock("@/hooks/useCoverage", () => ({
  usePayers: (...args: unknown[]) => mockUsePayers(...args),
  useUpdatePayer: () => ({ mutate: mockUpdate, isPending: false }),
  useCreatePayer: () => ({ mutate: mockCreate, isPending: false }),
  usePayerEnrollments: (...args: unknown[]) => mockUseEnrollments(...args),
  useRequestPayerEnrollments: () => ({
    mutate: mockRequestEnrollments,
    isPending: false,
    error: null,
  }),
  useEnrollmentTasks: (...args: unknown[]) => mockUseTasks(...args),
  useAnswerEnrollmentTask: () => ({ mutate: mockAnswerTask, isPending: false, error: null }),
  useRefreshPayerEnrollments: (...args: unknown[]) => mockUseRefreshEnrollments(...args),
  usePayerDirectory: (...args: unknown[]) => mockUseDirectory(...args),
}))

const AETNA: PayerResponse = {
  id: "payer-1",
  name: "Aetna",
  payer_id: "60054",
  clearinghouse_payer_id: null,
  is_carveout: false,
  carveout_of: null,
  enrollment_status: "none",
  enroll_eligibility: true,
  enroll_claims: true,
  enroll_remittance: false,
  timely_filing_days: 90,
  corrected_claim_days: 90,
  appeal_days: 180,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
}

const ENROLLMENTS: PayerEnrollmentListResponse = {
  enrollment_status: "pending",
  data: [
    {
      transaction_type: "835",
      vendor_request_id: "enr-1",
      status: "provider_action_required",
      instructions: "Sign the EFT authorization form and upload the signed copy.",
      updated_at: "2026-09-08T14:02:11Z",
    },
    {
      transaction_type: "837P",
      vendor_request_id: "enr-2",
      status: "live",
      instructions: null,
      updated_at: "2026-09-08T14:02:11Z",
    },
  ],
}

const TASKS: EnrollmentTaskListResponse = {
  status: "provider_action_required",
  data: [
    {
      id: "task-1",
      instructions: "Sign the EFT authorization form and upload the signed copy.",
      links: [
        { label: "EFT authorization form", url: "https://payer.example/eft.pdf", resolvable: false },
        { label: "Provider agreement", url: "https://ch.example/2024-09-01/documents/d1", resolvable: true },
      ],
      fields: [
        {
          key: "medicaid_id",
          label: "Medicaid provider id",
          field_type: "TEXT",
          description: null,
        },
        {
          key: "signed_eft_form",
          label: "Signed EFT authorization",
          field_type: "DOCUMENT",
          description: null,
        },
      ],
    },
  ],
  documents: [],
}

describe("PayersCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseDirectory.mockReturnValue({ data: undefined, isLoading: false })
    mockUsePayers.mockReturnValue({ data: { data: [AETNA], total: 1 } })
    mockUseEnrollments.mockReturnValue({ data: undefined })
    mockUseTasks.mockReturnValue({ data: undefined, isLoading: false, error: null })
    mockUseRefreshEnrollments.mockReturnValue({
      mutate: mockRefreshEnrollments,
      isPending: false,
      error: null,
      data: undefined,
    })
  })

  it("lists each payer with its filing window and enrollment status", () => {
    render(<PayersCard />)

    expect(screen.getByText("Aetna")).toBeInTheDocument()
    expect(
      screen.getByText(/Payer ID 60054 · files within 90 days · Not enrolled/),
    ).toBeInTheDocument()
  })

  it("fetches a payer's enrollments only once it is open", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    expect(mockUseEnrollments).not.toHaveBeenCalledWith("payer-1")

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(mockUseEnrollments).toHaveBeenCalledWith("payer-1")
    expect(screen.getByText(ENROLLMENT_HELP)).toBeInTheDocument()
  })

  it("says what enrolling for remittances moves, whether or not it is ticked", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    // Unticked, this sentence is the reason it is unticked; ticked, it is the
    // warning. Either way she reads it before the request is filed.
    expect(screen.getByLabelText("Receive remittances (ERAs)")).not.toBeChecked()
    expect(screen.getByText(REMITTANCE_WARNING)).toBeInTheDocument()
    expect(screen.getByLabelText("File claims")).toBeChecked()
    expect(screen.getByLabelText("Check eligibility")).toBeChecked()
  })

  it("saves a switch on its own, without touching the others", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))
    await user.click(screen.getByLabelText("Receive remittances (ERAs)"))

    expect(mockUpdate).toHaveBeenCalledWith({
      id: "payer-1",
      data: { enroll_remittance: true },
    })
  })

  it("has nothing to enroll for when she has asked for nothing", async () => {
    mockUsePayers.mockReturnValue({
      data: {
        data: [
          { ...AETNA, enroll_eligibility: false, enroll_claims: false, enroll_remittance: false },
        ],
        total: 1,
      },
    })
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(screen.getByRole("button", { name: "Enroll with payer" })).toBeDisabled()
  })

  it("shows each request, what the payer is waiting on, and the overall status", async () => {
    mockUseEnrollments.mockReturnValue({ data: ENROLLMENTS })
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(screen.getByText("Enrollment in progress")).toBeInTheDocument()
    expect(screen.getByText("Remittance")).toBeInTheDocument()
    expect(screen.getByText("Needs your action")).toBeInTheDocument()
    expect(
      screen.getByText("Sign the EFT authorization form and upload the signed copy."),
    ).toBeInTheDocument()
    expect(screen.getByText("Claims")).toBeInTheDocument()
    expect(screen.getByText("Live")).toBeInTheDocument()
  })

  it("asks for what the payer wants, on the request that is waiting", async () => {
    mockUseEnrollments.mockReturnValue({ data: ENROLLMENTS })
    mockUseTasks.mockReturnValue({ data: TASKS, isLoading: false, error: null })
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(screen.getByLabelText("Medicaid provider id")).toBeInTheDocument()
    expect(screen.getByLabelText("Signed EFT authorization")).toBeInTheDocument()
    // A link on the open web is an anchor the browser can just follow.
    expect(screen.getByRole("link", { name: "EFT authorization form" })).toHaveAttribute(
      "href",
      "https://payer.example/eft.pdf",
    )
    // One the clearinghouse hosts is not — it needs a key the browser lacks.
    expect(screen.getByRole("button", { name: "Provider agreement" })).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Provider agreement" })).toBeNull()
    // Only the request that is waiting on the practice grows a form.
    expect(mockUseTasks).toHaveBeenCalledWith("payer-1", "835")
    expect(mockUseTasks).not.toHaveBeenCalledWith("payer-1", "837P")
  })

  it("will not send half an answer", async () => {
    mockUseEnrollments.mockReturnValue({ data: ENROLLMENTS })
    mockUseTasks.mockReturnValue({ data: TASKS, isLoading: false, error: null })
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))
    expect(screen.getByRole("button", { name: "Send to the payer" })).toBeDisabled()

    await user.type(screen.getByLabelText("Medicaid provider id"), "MD-4471")

    expect(screen.getByRole("button", { name: "Send to the payer" })).toBeDisabled()
  })

  it("sends the typed answer and the PDF together", async () => {
    mockUseEnrollments.mockReturnValue({ data: ENROLLMENTS })
    mockUseTasks.mockReturnValue({ data: TASKS, isLoading: false, error: null })
    const pdf = new File(["%PDF-1.7"], "eft.pdf", { type: "application/pdf" })
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))
    await user.type(screen.getByLabelText("Medicaid provider id"), "MD-4471")
    await user.upload(screen.getByLabelText("Signed EFT authorization"), pdf)
    await user.click(screen.getByRole("button", { name: "Send to the payer" }))

    expect(mockAnswerTask).toHaveBeenCalledWith({
      payerRowId: "payer-1",
      transactionType: "835",
      taskId: "task-1",
      values: { medicaid_id: "MD-4471" },
      documents: { signed_eft_form: pdf },
    })
  })

  it("enrolls with the payer from its row", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))
    await user.click(screen.getByRole("button", { name: "Enroll with payer" }))

    expect(mockRequestEnrollments).toHaveBeenCalledWith({ payerRowId: "payer-1" })
  })

  it("opens a payer to edit the three deadlines, with the helper text", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(screen.getByLabelText("Timely filing (days)")).toHaveValue(90)
    expect(screen.getByLabelText("Corrected claim (days)")).toHaveValue(90)
    expect(screen.getByLabelText("Appeal (days)")).toHaveValue(180)
    expect(screen.getByText(DEADLINE_HELP)).toBeInTheDocument()
  })

  it("sends only the deadline that changed", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))
    const filing = screen.getByLabelText("Timely filing (days)")
    await user.clear(filing)
    await user.type(filing, "365")
    await user.tab()

    expect(mockUpdate).toHaveBeenCalledWith({
      id: "payer-1",
      data: { timely_filing_days: 365 },
    })
  })

  it("adds a payer by hand when the directory does not have it", async () => {
    // The fallback, not the ordinary path. The directory is authoritative
    // about what it knows, not about what exists, so a practice whose payer is
    // genuinely missing must not be stuck.
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Add a payer/ }))
    await user.click(screen.getByRole("button", { name: /isn.t listed/i }))
    await user.type(screen.getByLabelText("Name"), "Cigna")
    await user.type(screen.getByLabelText("Payer ID"), "62308")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(mockCreate).toHaveBeenCalledWith(
      { name: "Cigna", payer_id: "62308" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it("adds a payer from the directory, keeping its name and code", async () => {
    // The whole point: she never types 62308, and the name stored is the
    // directory's rather than one she invented, so the two cannot disagree.
    const user = userEvent.setup()
    mockUseDirectory.mockReturnValue({
      data: {
        unavailable: false,
        matches: [
          {
            payer_id: "62308",
            name: "CIGNA HEALTH AND LIFE INSURANCE COMPANY",
            aliases: [],
            requires_enrollment: ["835"],
            already_added: false,
          },
        ],
      },
      isLoading: false,
    })
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Add a payer/ }))
    await user.type(screen.getByLabelText(/find your insurer/i), "cigna")
    await user.click(screen.getByRole("button", { name: "Search" }))
    await user.click(screen.getByRole("button", { name: /^Add$/ }))

    expect(mockCreate).toHaveBeenCalledWith(
      { name: "CIGNA HEALTH AND LIFE INSURANCE COMPANY", payer_id: "62308" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it("says what a payer will require before she commits to it", async () => {
    const user = userEvent.setup()
    mockUseDirectory.mockReturnValue({
      data: {
        unavailable: false,
        matches: [
          {
            payer_id: "62308",
            name: "Cigna",
            aliases: [],
            requires_enrollment: ["837P", "835"],
            already_added: false,
          },
        ],
      },
      isLoading: false,
    })
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Add a payer/ }))
    await user.type(screen.getByLabelText(/find your insurer/i), "cigna")
    await user.click(screen.getByRole("button", { name: "Search" }))

    expect(screen.getByText(/needs enrollment for claims, remittance/i)).toBeInTheDocument()
  })

  it("offers no duplicate for a payer already on the list", async () => {
    const user = userEvent.setup()
    mockUseDirectory.mockReturnValue({
      data: {
        unavailable: false,
        matches: [
          {
            payer_id: "60054",
            name: "Aetna",
            aliases: [],
            requires_enrollment: [],
            already_added: true,
          },
        ],
      },
      isLoading: false,
    })
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Add a payer/ }))
    await user.type(screen.getByLabelText(/find your insurer/i), "aetna")
    await user.click(screen.getByRole("button", { name: "Search" }))

    expect(screen.getByText(/already added/i)).toBeInTheDocument()
  })

  it("tells her the directory is down rather than that her payer does not exist", async () => {
    // Different answers, different actions. Conflating them would send her to
    // check a name that was never the problem.
    const user = userEvent.setup()
    mockUseDirectory.mockReturnValue({
      data: { unavailable: true, matches: [] },
      isLoading: false,
    })
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Add a payer/ }))
    await user.type(screen.getByLabelText(/find your insurer/i), "aetna")
    await user.click(screen.getByRole("button", { name: "Search" }))

    expect(screen.getByText(/directory isn.t answering/i)).toBeInTheDocument()
    expect(screen.queryByText(/nothing matched/i)).not.toBeInTheDocument()
  })

  it("checks every payer's enrollments in one press", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: "Check for updates" }))

    expect(mockRefreshEnrollments).toHaveBeenCalledWith()
  })

  it("says nothing has moved when a pass changes nothing", () => {
    mockUseRefreshEnrollments.mockReturnValue({
      mutate: mockRefreshEnrollments,
      isPending: false,
      error: null,
      data: { changed: 0, checked_at: "2026-09-10T12:00:00Z", throttled: false },
    })
    render(<PayersCard />)

    expect(screen.getByText(/nothing has moved yet/)).toBeInTheDocument()
  })

  it("reports how many enrollments changed", () => {
    mockUseRefreshEnrollments.mockReturnValue({
      mutate: mockRefreshEnrollments,
      isPending: false,
      error: null,
      data: { changed: 2, checked_at: "2026-09-10T12:00:00Z", throttled: false },
    })
    render(<PayersCard />)

    expect(screen.getByText(/2 enrollments changed/)).toBeInTheDocument()
  })
})
