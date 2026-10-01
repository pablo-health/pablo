# Certificate Manager and Compute fixtures

Captured from the real APIs through the Python clients the code uses
(`certificate_manager_v1` and `compute_v1`, serialised with `Type.to_json`), so
they are in the shape `practice_domain_gcp` parses. Scrubbed afterwards: the
project id and number, hostnames, the authorisation's uuid, resource names, the
URL map's id and fingerprint, and the PEM chain are replaced. The structure is
unchanged.

| File | What it is |
|---|---|
| `dns_authorization.json` | A DNS authorisation and its `_acme-challenge` CNAME |
| `certificate.json` | A Google-managed certificate while it was provisioning, with its authorisation attempt still `AUTHORIZING` |
| `certificate_active.json` | The same certificate once active (`AUTHORIZED` attempt) |
| `certificate_map_entry.json` | A certificate map entry for one hostname |
| `url_map.json` | A URL map with one path matcher, one host rule, and a redirect default |

## The failed attempt is derived, not captured

There is no capture of a certificate behind a failed authorisation attempt. An
attempt run before the `_acme-challenge` record existed was seen to fail, but
it could not be reproduced on demand: a fresh certificate with no record sat in
`AUTHORIZING` and never failed. The failed cases in `test_practice_domain_gcp.py`
therefore start from `certificate.json` and change only state fields:
`managed.state`, and on the attempt `state`, `failureReason` and `details`.

For reference only (not a fixture): a real failure, read through the REST API,
showed the attempt as `state: FAILED`, `failureReason: CONFIG`, and a
`troubleshooting` block with `cname {name, expectedData}` and
`issues: [CNAME_MISMATCH]`. The Python client has no `troubleshooting` field
and drops it, so the code decides from the attempt's state and reason, and from
its own DNS check of the `_acme-challenge` record.
