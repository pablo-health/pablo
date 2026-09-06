# Clearinghouse response fixtures

Recorded from the clearinghouse's test environment with a test API key. Every
value is synthetic: the patient is the vendor's documented example person, the
provider is the vendor's documented dummy NPI, the tax id is a random dummy
EIN created for the test payer enrollment, and no request ever reached a real
payer. Nothing here is PHI.

| File | What it is |
|---|---|
| `837p_request_test_payer.json` | a professional claim request body the API accepts |
| `837p_submission_success*.json` | the synchronous accept: `status`, `claimReference` (vendor claim id, echoed control numbers) |
| `837p_submission_edit_rejected_*.json` | HTTP 400 edit rejections: `status: ERROR`, `errors[]` with `code`, `description`, `followupAction` |
| `polling_transactions_277_and_835.json` | the polling endpoint listing an outbound 837, an inbound 277CA and an inbound 835 |
| `835_report_paid_in_full.json` | the 835 as JSON from the report endpoint |
| `enrollment_create_provider.json`, `enrollment_create_enrollment_835.json` | provider record and a transaction enrollment that went live |
| `payer_search_test_payer.json` | the payer directory's answer to a search for the test payer |
| `eligibility_271_active.json` | a 271 for the vendor's documented mock "active coverage" member |
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
