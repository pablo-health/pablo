# HIPAA Security: TLS on the VM install

Most of this document moved. The full picture of a self-hosted install, what
the installer builds and what is left to you, is in the
[Self-hosting HIPAA guide](SELF_HOSTING_HIPAA_GUIDE.md). This page keeps
the transmission-security facts (45 CFR § 164.312(e)(1)) for the
single-VM install.

## How traffic is encrypted

| Hop | Control | Source |
|---|---|---|
| Browser to VM | Caddy terminates TLS with a Let's Encrypt certificate it obtains and renews itself. Ports 80 and 443 are the only ones open to the internet; 80 exists for the certificate challenge | `deploy/vm/Caddyfile`, `deploy/vm/lib/vm.sh` |
| Caddy to backend and frontend | Plain HTTP on a private Docker network (`172.30.0.0/24`) inside the VM | `deploy/vm/compose.yaml` |
| Frontend server to backend | Through Caddy, by the app's own hostname, so it arrives like any other HTTPS request | `deploy/vm/compose.yaml`, `deploy/vm/lib/env.sh` |
| Backend to Postgres | Loopback and the Docker gateway only, SCRAM password; nothing leaves the VM | `deploy/vm/bootstrap.sh` |
| VM to Google APIs | Google's TLS endpoints, authenticated by the VM's service account | — |

## What the backend enforces

`HTTPSEnforcementMiddleware` (`backend/app/middleware/security.py`):

- Outside `ENVIRONMENT=development`, a request that did not arrive over
  HTTPS is answered `400 HTTPS required`. Only the platform health-probe
  paths are exempt.
- `X-Forwarded-Proto` and `X-Forwarded-SSL` are believed only when the
  immediate peer is listed in `TRUSTED_PROXY_IPS`. The VM sets it to Caddy's
  fixed address, `172.30.0.10`.
- There is no `ENFORCE_HTTPS` switch; `ENVIRONMENT` decides.

`SecurityHeadersMiddleware` sends HSTS (`max-age=63072000; includeSubDomains;
preload`, from `hsts_*` in `settings.py`) and the other defensive headers.

## Check it

```bash
curl -I https://<host>/api/health | grep -i strict-transport-security
curl -sI http://<host>/api/health | head -1    # Caddy redirects to HTTPS
openssl s_client -connect <host>:443 -tls1_2 </dev/null | grep -i protocol
```

[SSL Labs](https://www.ssllabs.com/ssltest/) gives an outside view of the
TLS versions and suites Caddy offers.

## If the certificate stops renewing

Caddy retries on its own. A browser warning after the 90-day certificate
lifetime means it could not complete the challenge: check that port 80 is
still open, that DNS still points at the VM's static address, and Caddy's
log (`docker logs pablo-caddy-1`). Nothing in Google Cloud needs to change.
