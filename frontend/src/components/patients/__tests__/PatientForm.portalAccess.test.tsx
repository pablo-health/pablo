// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// The first-client portal question, for a clinician who may not change the
// portal or whose permission is not known yet: the form must not ask.

import { beforeEach, describe, it, expect, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { PatientForm } from "../PatientForm"
import * as patientsApi from "@/lib/api/patients"
import type { PatientResponse } from "@/types/patients"

vi.mock("@/lib/api/patients")
vi.mock("@/hooks/usePeopleTerm", async (orig) => {
  const { peopleWords } = await import("@/lib/peopleTerm")
  return {
    ...(await orig<typeof import("@/hooks/usePeopleTerm")>()),
    usePeopleTerm: () => peopleWords("clients"),
  }
})
vi.mock("@/lib/featureGates", () => ({
  useFeature: (name: string) => name === "patient_portal",
}))
vi.mock("@/lib/api/intakePackets", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/intakePackets")>()),
  listIntakeTemplates: vi.fn().mockResolvedValue([]),
}))
vi.mock("@/lib/api/portalAccess", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalAccess")>()),
  getPortalAccess: vi.fn().mockResolvedValue({
    patient_id: "patient-new",
    invite_outstanding: false,
    live_sessions: 0,
    portal_enabled: true,
    revoked_at: null,
  }),
}))
vi.mock("@/lib/api/inviteTemplate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/inviteTemplate")>()),
  getInviteTemplate: vi.fn().mockResolvedValue({ editable: false }),
}))

// The practice has never decided, so an admin would be asked.
const mockSavePortalSettings = vi.fn()
vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: vi.fn().mockResolvedValue({
    enabled: false,
    decided: false,
    modules: { intake: true, messaging: true, appointments: true, refills: true },
  }),
  savePortalSettings: (...a: unknown[]) => mockSavePortalSettings(...a),
}))

let userStatus: { data?: { is_practice_admin: boolean }; isError: boolean }
vi.mock("@/components/settings/useSettingsPreferences", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/settings/useSettingsPreferences")>()),
  useSettingsUserStatus: () => userStatus,
}))

const robin: PatientResponse = {
  id: "patient-new",
  first_name: "Robin",
  last_name: "Reyes",
  email: null,
  phone: null,
  status: "active",
  date_of_birth: null,
  diagnosis: null,
  session_count: 0,
  last_session_date: null,
  next_session_date: null,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
  deleted_at: null,
  restore_deadline: null,
  address_line1: null,
  address_line2: null,
  city: null,
  state: null,
  postal_code: null,
  sex: null,
}

async function createRobin(): Promise<QueryClient> {
  vi.mocked(patientsApi.createPatient).mockResolvedValue(robin)
  vi.mocked(patientsApi.getPatient).mockResolvedValue(robin)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={queryClient}>
      <PatientForm mode="create" open={true} onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  )
  await user.type(screen.getByLabelText(/first name/i), "Robin")
  await user.type(screen.getByLabelText(/last name/i), "Reyes")
  await user.click(screen.getByRole("button", { name: /create client/i }))
  return queryClient
}

beforeEach(() => {
  mockSavePortalSettings.mockClear()
})

describe("PatientForm, for a clinician who cannot change the portal", () => {
  it("does not ask about the portal and goes on to the next step", async () => {
    userStatus = { data: { is_practice_admin: false }, isError: false }
    await createRobin()

    expect(await screen.findByTestId("new-client-next-step")).toBeInTheDocument()
    expect(screen.queryByTestId("portal-offer-prompt")).not.toBeInTheDocument()
    expect(mockSavePortalSettings).not.toHaveBeenCalled()
  })
})

describe("PatientForm, before the clinician's status is known", () => {
  it("holds the next step while the status loads", async () => {
    userStatus = { data: undefined, isError: false }
    const queryClient = await createRobin()

    // Wait for the portal settings, so only the status is still unknown.
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(screen.getByText("Loading")).toBeInTheDocument()
    expect(screen.queryByTestId("new-client-next-step")).not.toBeInTheDocument()
    expect(screen.queryByTestId("portal-offer-prompt")).not.toBeInTheDocument()
    expect(mockSavePortalSettings).not.toHaveBeenCalled()
  })

  it("skips the question when the status request fails", async () => {
    userStatus = { data: undefined, isError: true }
    await createRobin()

    expect(await screen.findByTestId("new-client-next-step")).toBeInTheDocument()
    expect(screen.queryByTestId("portal-offer-prompt")).not.toBeInTheDocument()
    expect(mockSavePortalSettings).not.toHaveBeenCalled()
  })
})
