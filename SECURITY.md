# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do NOT:**
- Open a public GitHub issue
- Post about it on social media
- Exploit the vulnerability

**Do:**
- Email security concerns to: [security@pablo.health](mailto:security@pablo.health)
- Include a detailed description of the vulnerability
- Provide steps to reproduce if possible
- Allow reasonable time for us to respond and fix

## What to Report

- Authentication or authorization bypasses
- Data exposure or leakage
- Injection vulnerabilities (SQL, XSS, command injection)
- Cryptographic weaknesses
- HIPAA compliance concerns
- Any issue that could expose PHI (Protected Health Information)

## Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 7 days
- **Resolution target**: Depends on severity
  - Critical: 24-72 hours
  - High: 7 days
  - Medium: 30 days
  - Low: 90 days

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

This project follows security best practices:

- All secrets managed via environment variables (never committed)
- PHI encrypted at rest and in transit
- Regular dependency updates for security patches
- Manual security review on all PRs (automated scanning planned)
- HIPAA-compliant infrastructure when deployed

## Acknowledgments

We appreciate security researchers who help keep this project safe. With your permission, we'll acknowledge your contribution in our release notes.
