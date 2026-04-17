# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Pablo, **please do not open a public issue.**

Instead, please report it through [GitHub's private vulnerability reporting](https://github.com/pablo-health/pablo/security/advisories/new):

1. Go to the [Security Advisories](https://github.com/pablo-health/pablo/security/advisories) page
2. Click **"Report a vulnerability"**
3. Fill in the details and submit

If you are unable to use GitHub's reporting, you may email [security@pablo.health](mailto:security@pablo.health) with the subject line "Pablo Vulnerability Disclosure".

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Affected component(s) and version(s)
- Potential impact (e.g., data exposure, privilege escalation, PHI leakage)
- Any suggested fix or mitigation (optional)

### Response Timeline

- A maintainer will **acknowledge** the report within **48 hours**
- A detailed response with next steps will follow within **7 business days**
- A fix or mitigation will be developed based on severity:
  - Critical: 24-72 hours
  - High: 7 days
  - Medium: 30 days
  - Low: 90 days

### Disclosure Policy

We follow a [coordinated vulnerability disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure) process:

- Reporters are asked to keep vulnerability details confidential until a fix is released
- We will coordinate a disclosure timeline with the reporter
- Security advisories will be published via [GitHub Security Advisories](https://github.com/pablo-health/pablo/security/advisories) once a fix is available
- Credit will be given to reporters unless they prefer to remain anonymous

## What to Report

- Authentication or authorization bypasses
- Data exposure or leakage
- Injection vulnerabilities (SQL, XSS, command injection)
- Cryptographic weaknesses
- HIPAA compliance concerns
- Any issue that could expose PHI (Protected Health Information)

## Scope

This security policy applies to:
- The main application codebase
- Official Docker images
- Deployment configurations

Out of scope:
- Third-party dependencies (report to their maintainers)
- Social engineering attacks
- Physical security

## Security Practices

This project handles sensitive clinical data and follows these security practices:

- **Encryption**: PHI encrypted at rest and in transit
- **Secrets management**: All secrets managed via environment variables (never committed)
- **Dependency scanning**: Automated via [Trivy](https://github.com/aquasecurity/trivy) and [Dependabot](https://github.com/pablo-health/pablo/security/dependabot)
- **Static analysis**: [CodeQL](https://github.com/pablo-health/pablo/security/code-scanning) (SAST) and Trivy misconfiguration scanning
- **CI enforcement**: All security checks run on every PR
- **HIPAA-compliant infrastructure** when deployed

## Dependency Vulnerability Management

Pablo's HIPAA posture treats third-party vulnerabilities the same as first-party ones: risk-assess on detection, patch inside an SLA, and document any exception. This section is the written policy that satisfies 45 CFR §164.308(a)(1)(ii)(B) (risk management) and §164.308(a)(5)(ii)(B) (protection from malicious software) for dependencies.

### Detection

- `npm audit` runs in CI for every PR (frontend + marketing).
- GitHub Dependabot alerts on `package-lock.json`, `pyproject.toml` / `poetry.lock`, and Dockerfiles.
- Trivy scans built container images.

A failing CI audit blocks merge. That gate is the primary control.

### Patch SLA

Measured from the time the advisory becomes publicly known (CVE publication or Dependabot alert timestamp, whichever is earlier):

| Severity | SLA | Trigger |
|---|---|---|
| **Critical** | Patch and release within **7 days** | RCE, auth bypass, direct PHI exposure, or any advisory reachable in our code path |
| **High** | Patch within **30 days** | Exploitable under realistic authenticated attack, or in a reachable code path |
| **Moderate** | Patch within **90 days** | Usually bundled into the next routine dependency sweep |
| **Low / Informational** | Next routine sweep | No dedicated tracking required |

If a patch exists and is a non-breaking upgrade (e.g., `npm audit fix` works cleanly), the evidence of compliance is the commit and the passing CI run. **No separate memo is required** — the patch *is* the record.

### When analysis IS required

A written note in `docs/pentest/VULNERABILITY_EXCEPTIONS.md` is required only when:

1. **We can't patch inside the SLA** — no upgrade path, breaking change blocked by pinned peer, etc. Record the compensating control and the revisit date.
2. **We choose not to patch** — false positive, advisory doesn't apply to our usage (e.g., a parser advisory on bytes we never feed untrusted input to). Record *why*.
3. **The advisory is Critical** — a one-line note confirming the code path is reachable/not-reachable from PHI-touching routes. This is the only case where we add paperwork even to a patched Critical, because if exploited before the patch landed it could trigger §164.410 breach analysis.

For Moderate/High advisories that are patched inside SLA, the commit message is enough. Do not waste cycles on per-CVE memos.

### If a vulnerability is exploited in our environment

This is the §164.308(a)(6)(ii) ("response and reporting") path, not this policy. Open a security incident, preserve logs, and run the §164.402 four-factor breach analysis. If ePHI was accessed, §164.410 notification timelines apply.

### Exception log

Current exceptions: see [docs/pentest/VULNERABILITY_EXCEPTIONS.md](docs/pentest/VULNERABILITY_EXCEPTIONS.md).

## Staying Notified

To receive security notifications:
- **Watch this repo** → Custom → check "Security alerts"
- Or subscribe to [GitHub Security Advisories](https://github.com/pablo-health/pablo/security/advisories) for this repo

When we publish an advisory, GitHub automatically notifies watchers and lists it in the global Advisory Database.

## Acknowledgments

We appreciate security researchers who help keep this project safe. With your permission, we'll acknowledge your contribution in our release notes.
