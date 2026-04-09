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

## Staying Notified

To receive security notifications:
- **Watch this repo** → Custom → check "Security alerts"
- Or subscribe to [GitHub Security Advisories](https://github.com/pablo-health/pablo/security/advisories) for this repo

When we publish an advisory, GitHub automatically notifies watchers and lists it in the global Advisory Database.

## Acknowledgments

We appreciate security researchers who help keep this project safe. With your permission, we'll acknowledge your contribution in our release notes.
