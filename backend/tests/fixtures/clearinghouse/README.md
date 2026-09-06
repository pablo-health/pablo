# Clearinghouse response fixtures

Recorded from the clearinghouse's test environment with a test API key. Every
value is synthetic: the patient is the vendor's documented example person, the
provider is the vendor's documented dummy NPI, the tax id is a random dummy
EIN created for the test payer enrollment, and no request ever reached a real
payer. Nothing here is PHI. Contact emails in the recordings are replaced
with a synthetic address (`billing@example.test`); no real person's address
appears in any fixture.

| File | What it is |
|---|---|
| `837p_request_test_payer.json` | a professional claim request body the API accepts |
| `837p_submission_success*.json` | the synchronous accept: `status`, `claimReference` (vendor claim id, echoed control numbers) |
| `837p_submission_success_dependent_test_payer.json` | the accept for a claim whose patient is a child on the policy holder's plan — `subscriber` + `dependent` loops, both people made up |
| `837p_submission_edit_rejected_*.json` | HTTP 400 edit rejections: `status: ERROR`, `errors[]` with `code`, `description`, `followupAction` |
| `polling_transactions_277_and_835.json` | the polling endpoint listing an outbound 837, an inbound 277CA and an inbound 835 |
| `transaction_outbound_837.json`, `transaction_inbound_277.json` | single transaction documents from the transaction endpoint: the outbound 837 for a test-payer claim (`CLM-01` is the control number, `BHT-03` the vendor's claim id) and the inbound 277CA that acknowledged it (`TRN-02` echoes the control number) |
| `277ca_report_clearinghouse_forwarded.json` | the 277CA as JSON from the report endpoint: the clearinghouse (`entityIdentifierCode: AY`, `STEDI INC`) saying the claim was received and forwarded to the payer (`A1`/`16`). The subscriber's first name is normalised to the vendor's documented example person |
| `835_report_paid_in_full.json` | the 835 as JSON from the report endpoint |
| `enrollment_create_provider.json`, `enrollment_create_enrollment_835.json` | provider record and a transaction enrollment that went live |
| `payer_search_test_payer.json` | the payer directory's answer to a search for the test payer |
| `eligibility_271_active.json` | a 271 for the vendor's documented mock "active coverage" member. Asking with `encounter.serviceTypeCodes: ["MH"]` returns this same body, byte-for-byte apart from ids (checked 2026-09-06), so one recording serves both the plan-level and the mental-health inquiry |
| `eligibility_271_inactive.json` | a 271 for the vendor's documented mock "inactive coverage" member (`planStatus` code 6), asked with service type `MH` |
| `eligibility_271_aaa_invalid_member_id.json` | HTTP 200 with no `planStatus` and a top-level `errors[]` carrying AAA 72, for a made-up member id the mock payer does not know |

Regenerate with a test API key against the vendor's test payer; the shapes are
stable across their dated API versions. The live suite under
`backend/tests_integration/clearinghouse_live/` shape-diffs each of these
against the vendor's current answer, so drift shows up there first.

The enrollment fixtures are the one pair a test key cannot regenerate: the
vendor's enrollment API refuses test-mode keys outright (`403 access_denied`),
so those were captured through the account's production-mode credentials
during the one-time test-payer enrollment. The provider record and enrollment
they describe already exist on the account — reuse them, never create another.

## Constructed fixtures

The files below were NOT captured from a live call — the vendor's generic
`{code, message}` error envelope is hand-built from its documented shape.

| File | What it is |
|---|---|
| `error_invalid_request_body.json`, `error_account_not_provisioned.json` | the vendor's generic `{code, message}` error envelope |
| `error_request_changed.json` | the 422 an idempotency key gets when reused with a different body; the live lane sees this `code`, the `message` is illustrative |
| `error_access_denied.json` | the 403 the enrollment API answers a test-mode key with; same caveat |
| `enrollment_provider_action_required.json` | the recorded 835 enrollment moved to `PROVIDER_ACTION_REQUIRED`, with a `tasks[]` entry and `reason` built from the vendor's documented enrollment schema — the state a test key can never produce, and the one the reminder flow is built on |
| `277ca_report_payer_accepted.json`, `277ca_report_payer_rejected.json` | the recorded 277CA re-sourced to the payer (`entityIdentifierCode: PR`): one accepting the claim into adjudication (`A2`/`20`, with a made-up `tradingPartnerClaimNumber`), one rejecting it for invalid subscriber information (`A7`/`21` and `A7`/`164`, action `U`). The test payer only ever sends a clearinghouse-sourced 277CA, so the payer's own acknowledgement is built from its shape |
| `webhook_transaction_processed.json` | a `transaction.processed` event as the vendor's event destination posts it, built from the documented event schema |
| `eligibility_271_carveout_behavioral.json` | a 271 whose behavioral benefit is administered by somebody other than the payer on the card. None of the vendor's mock members carries one, so this is `eligibility_271_active.json` with its recorded pharmacy carve-out line (`benefitsInformation[].code == "U"`, service type `88`, entity OPTUMRX — the shape the vendor's own mock uses for "contact this other entity") re-pointed at service types `MH`/`A4` and a made-up third-party administrator (`EXAMPLE BEHAVIORAL HEALTH`, payer id `EXBH1`). The vendor's per-call ids are blanked so nobody mistakes it for a capture |
