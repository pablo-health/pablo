# Domain Connect fixtures

Captured from real DNS providers on 2026-10-01 with read-only DNS queries and
HTTP GETs. Bodies are exactly as received; the only change is the customer
domain in each settings request path, replaced with an `example.*` name.
Response headers are dropped. Squarespace's per-request `contextId` is
replaced with `test-context`.

Each HTTP fixture is `{captured, request, status, content_type, body}`, with
the body as the raw text.

| File | What it is |
|---|---|
| `dns_txt.json` | TXT answers. `_domainconnect.<domain>` for a Squarespace-, a Cloudflare- and an IONOS-hosted domain (the Squarespace one arrives through a CNAME; this is the final TXT answer, as the resolver returns it). `dev1.dc.pablo.health` is the published public key for the `dev1` key host: two records, `p=` fragments in the order DNS returned them, which is not `p` order. |
| `squarespace_settings.json` | the settings answer for a Squarespace domain: `providerDisplayName`, and `urlSyncUX` equal to `urlAPI` |
| `cloudflare_settings.json` | the settings answer for a Cloudflare domain: a settings host with a path, pretty-printed JSON |
| `ionos_settings_not_found.json` | IONOS answering 404 with an empty body for a domain it does not manage for a customer |
| `squarespace_template_supported.json` | 200: Squarespace has a template (a public one from another service) |
| `squarespace_template_unsupported.json` | 404: Squarespace does not have the template |
| `cloudflare_template_unsupported.json` | 404: Cloudflare does not have the template |
| `kms_sample_input.txt`, `kms_sample_signature.bin` | a canonical apply query (test values only), and its RS256 signature made by the real `dev1` Cloud KMS key version with `gcloud kms asymmetric-sign --digest-algorithm sha256`. The test checks it against the key in `dns_txt.json`. |

To recapture, repeat the same GETs and `dig +short TXT` queries, keep the
bodies verbatim and replace only the customer domains.
