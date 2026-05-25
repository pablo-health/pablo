/**
 * "Given X" helpers — declarative state setup against the deployed
 * backend.
 *
 * Tests should read close to plain English: "given a patient, when I
 * upload a document, then it appears in the list." Each helper hits
 * the real API as the test user, returns the created resource's
 * identifying bits, and is responsible for being collision-safe
 * (unique IDs) — the shared worker user accumulates state across
 * tests by design.
 *
 * Add new verbs here as patterns emerge. Per-test cleanup is
 * intentionally NOT provided — tests should tolerate accumulated
 * state, not clean it up.
 */
import { randomBytes } from "node:crypto";

import type { EnrolledUser } from "./auth";

type ApiUser = Pick<EnrolledUser, "apiUrl" | "getIdToken">;

async function postJson<T>(args: {
  user: ApiUser;
  path: string;
  body: Record<string, unknown>;
}): Promise<T> {
  const idToken = await args.user.getIdToken();
  const resp = await fetch(`${args.user.apiUrl}${args.path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${idToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(args.body),
  });
  if (!resp.ok) {
    throw new Error(
      `POST ${args.path} failed: ${resp.status} ${await resp.text()}`,
    );
  }
  return (await resp.json()) as T;
}

export type CreatedPatient = {
  id: string;
  first_name: string;
  last_name: string;
};

export async function givePatient(
  user: ApiUser,
  opts: { firstName?: string; lastName?: string } = {},
): Promise<CreatedPatient> {
  // 8-hex noise on the last name so repeated calls within a worker
  // produce distinct rows + selectable UI text without a teardown.
  const noise = randomBytes(4).toString("hex");
  const first = opts.firstName ?? "E2E";
  const last = opts.lastName ?? `Patient-${noise}`;
  return postJson<CreatedPatient>({
    user,
    path: "/api/patients",
    body: {
      first_name: first,
      last_name: last,
      status: "active",
    },
  });
}

/**
 * Group of helpers exported as a namespace so call sites read as
 * `scenarios.givePatient(user, ...)`.
 */
export const scenarios = {
  givePatient,
};
