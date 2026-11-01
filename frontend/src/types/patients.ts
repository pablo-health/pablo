// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient API Types
 *
 * Generated from the backend's OpenAPI schema (`./api/schema.gen.ts`), so a
 * field the backend adds, drops or retypes surfaces here as a type error.
 * Regenerate with `make api-schema`.
 */

import type { components, operations } from "./api/schema.gen"

type Schemas = components["schemas"]

/**
 * A patient record. `deleted_at` and `restore_deadline` are set only on rows
 * from the recently-deleted listing (`include_deleted=recent`).
 */
export type PatientResponse = Schemas["PatientResponse"]

/**
 * The allergy record. "nkda" (no known drug allergies) is its own state,
 * distinct from "not_recorded"; "recorded" always has at least one entry.
 */
export type AllergyStatus = Schemas["UpdateAllergiesRequest"]["status"]

/** An allergy as the chart shows it; every field is sent. */
export type AllergyEntry = Schemas["AllergyEntry-Output"]

/** An allergy as it is saved; only `substance` is required. */
export type AllergyEntryInput = Schemas["AllergyEntry-Input"]

export type UpdateAllergiesRequest = Schemas["UpdateAllergiesRequest"]

export type PatientListResponse = Schemas["PatientListResponse"]

export type CreatePatientRequest = Schemas["CreatePatientRequest"]

/** Only the fields provided are updated. */
export type UpdatePatientRequest = Schemas["UpdatePatientRequest"]

export type DeletePatientResponse = Schemas["DeletePatientResponse"]

/**
 * Query parameters for listing patients. `include_deleted: "recent"` lists
 * soft-deleted patients still inside the 30-day undo window instead.
 */
export type PatientListParams = NonNullable<
  operations["list_patients_api_patients_get"]["parameters"]["query"]
>
