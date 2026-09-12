// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pins this form's own validation against the API's.
 *
 * Availability rule params used to be hand-kept in three places — the
 * backend request models, the natural-language parser, and this form —
 * agreeing only by convention. A convention drifts, and drift here is
 * silent: a form that sends `minute` instead of `minutes` produces a
 * buffer rule that enforces nothing and still reads as configured.
 *
 * There is one authority now, the backend's tagged union, which emits
 * availabilityRuleParams.schema.json. The form keeps its own validate()
 * because it has to say something before the request goes out; these
 * tests fail when what it says stops matching what the API accepts.
 * Regenerate the schema with:
 *   poetry run python backend/scripts/regen_availability_param_schema.py
 */

import { describe, it, expect } from "vitest"
import schema from "@/types/availabilityRuleParams.schema.json"
import { RULE_TYPES } from "@/types/availability"
import type { RuleType } from "@/types/availability"
import { buildParams, defaultFields, validate } from "../AvailabilitySettings"

interface ParamsSchema {
  properties: Record<string, { minimum?: number; pattern?: string }>
  required?: string[]
  additionalProperties: boolean
}

const SAMPLE_DATES = ["2026-03-01"]

/** The params sub-schema for one rule type, followed through the union. */
function paramsSchemaFor(ruleType: RuleType): ParamsSchema {
  const defs = schema.$defs as Record<string, unknown>
  const mapping = schema.discriminator.mapping as Record<string, string>
  const member = defs[mapping[ruleType].replace("#/$defs/", "")] as {
    properties: { params: { $ref: string } }
  }
  return defs[member.properties.params.$ref.replace("#/$defs/", "")] as ParamsSchema
}

describe("availability rule params contract", () => {
  it("covers exactly the rule types the API discriminates on", () => {
    expect(new Set(Object.keys(schema.discriminator.mapping))).toEqual(new Set(RULE_TYPES))
  })

  it.each(RULE_TYPES)("%s sends only keys the API accepts", (ruleType) => {
    const params = buildParams(ruleType, defaultFields(ruleType), SAMPLE_DATES)
    const { properties, additionalProperties } = paramsSchemaFor(ruleType)

    // The API forbids unknown keys, so an invented or misspelled one here
    // is a 422 rather than a rule that quietly enforces nothing.
    expect(additionalProperties).toBe(false)
    expect(Object.keys(params).filter((key) => !(key in properties))).toEqual([])
  })

  it.each(RULE_TYPES)("%s sends every key the API requires", (ruleType) => {
    const params = buildParams(ruleType, defaultFields(ruleType), SAMPLE_DATES)
    const required = paramsSchemaFor(ruleType).required ?? []

    for (const key of required) {
      expect(Object.keys(params)).toContain(key)
    }
  })

  it.each([
    ["max_per_day", "max"],
    ["buffer_before", "minutes"],
    ["buffer_after", "minutes"],
  ] as [RuleType, string][])(
    "%s refuses a %s below the API's minimum",
    (ruleType, field) => {
      const minimum = paramsSchemaFor(ruleType).properties[field].minimum
      expect(minimum).toBeDefined()

      const fields = { ...defaultFields(ruleType), [field]: String((minimum as number) - 1) }
      expect(validate(ruleType, fields, [])).not.toBeNull()

      const atMinimum = { ...defaultFields(ruleType), [field]: String(minimum) }
      expect(validate(ruleType, atMinimum, [])).toBeNull()
    }
  )

  it("refuses an end that is not after its start, as the API does", () => {
    const range = paramsSchemaFor("working_hours")
    expect(Object.keys(range.properties)).toContain("end")

    const backwards = { day_of_week: "0", start: "17:00", end: "09:00" }
    const zeroLength = { day_of_week: "0", start: "09:00", end: "09:00" }
    expect(validate("working_hours", backwards, [])).not.toBeNull()
    expect(validate("working_hours", zeroLength, [])).not.toBeNull()
    expect(validate("block_time_range", { start: "12:00", end: "13:00" }, [])).toBeNull()
  })

  it("refuses an empty date list, as the API's minimum length does", () => {
    expect(validate("block_specific_dates", {}, [])).not.toBeNull()
    expect(validate("block_specific_dates", {}, SAMPLE_DATES)).toBeNull()
  })

  it("refuses an end date before its start date, as the API does", () => {
    const fields = { start_date: "2026-03-10", end_date: "2026-03-01" }
    expect(validate("block_date_range", fields, [])).not.toBeNull()
  })
})
