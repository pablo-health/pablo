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

Regenerate with a test API key against the vendor's test payer; the shapes are
stable across their dated API versions.
