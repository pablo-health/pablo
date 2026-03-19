# HIPAA Security - TLS/HTTPS Enforcement

This document covers the TLS encryption requirements for HIPAA compliance and how they are implemented in the Pablo.

## HIPAA Requirement

**HIPAA Security Rule § 164.312(e)(1) - Transmission Security**: Implement technical security measures to guard against unauthorized access to electronic protected health information (ePHI) that is being transmitted over an electronic communications network.

**Implementation Specification**: Encryption (Addressable) - Implement a mechanism to encrypt ePHI whenever deemed appropriate.

**Industry Standard**: TLS 1.2 or higher is required for all PHI transmission. TLS 1.0 and 1.1 are deprecated and must not be used.

## Implementation

### Middleware Components

The platform enforces HTTPS/TLS through two middleware components:

1. **HTTPSEnforcementMiddleware** (`backend/app/middleware.py`)
   - Rejects all HTTP requests in production
   - Supports reverse proxy configurations (X-Forwarded-Proto, X-Forwarded-SSL headers)
   - Allows localhost HTTP in development mode only

2. **SecurityHeadersMiddleware** (`backend/app/middleware.py`)
   - Adds HTTP Strict Transport Security (HSTS) headers
   - Implements defense-in-depth security headers (CSP, X-Frame-Options, etc.)

### Configuration

Settings are managed in `backend/app/config.py`:

```python
# Environment
environment: str = "development"  # Set to "production" in production

# Security - HIPAA TLS Requirements
enforce_https: bool = True  # Must be True in production
hsts_max_age: int = 31536000  # 1 year (recommended by OWASP)
hsts_include_subdomains: bool = True
hsts_preload: bool = True
```

### Environment Variables

Configure via `.env` file:

```bash
ENVIRONMENT=production
ENFORCE_HTTPS=true
HSTS_MAX_AGE=31536000
HSTS_INCLUDE_SUBDOMAINS=true
HSTS_PRELOAD=true
```

## TLS Certificate Management

### Development Environment

For local development, you can use:

1. **Self-signed certificates** (for testing only)
   ```bash
   openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
   ```

2. **mkcert** (easier local HTTPS)
   ```bash
   brew install mkcert
   mkcert -install
   mkcert localhost 127.0.0.1
   ```

3. **HTTP allowed on localhost** - The middleware automatically allows HTTP on localhost in development mode

### Production Environment (Google Cloud Run)

#### TLS Termination

Google Cloud Run provides **automatic TLS termination**:

- Google Cloud Load Balancer handles TLS termination
- Automatically provisions and renews Google-managed SSL certificates
- Supports TLS 1.2 and TLS 1.3 (TLS 1.0/1.1 disabled by default)
- Certificate rotation is handled automatically

#### Certificate Options

**Option 1: Google-managed certificates (Recommended)**
- Automatic provisioning and renewal
- No manual certificate management required
- Supports multiple domains
- Free of charge

Setup:
```bash
# Cloud Run automatically provisions certificates for custom domains
gcloud run services add-iam-policy-binding therapy-platform-api \
  --region=us-central1 \
  --member=allUsers \
  --role=roles/run.invoker
```

**Option 2: Custom certificates**
If you need to use your own certificates (e.g., Extended Validation certs):

```bash
# Upload certificate to Google Cloud
gcloud compute ssl-certificates create therapy-platform-cert \
  --certificate=path/to/cert.pem \
  --private-key=path/to/key.pem

# Use with load balancer
gcloud compute target-https-proxies create therapy-platform-proxy \
  --ssl-certificates=therapy-platform-cert \
  --url-map=therapy-platform-lb
```

#### Certificate Renewal

**Google-managed certificates**:
- Automatic renewal before expiration
- No action required
- Monitor in Cloud Console: Networking → Load Balancing → Certificates

**Custom certificates**:
- Set up monitoring alerts 30 days before expiration
- Use Cloud Scheduler to automate renewal checks
- Recommended: Use Let's Encrypt with certbot for auto-renewal

```bash
# Example: Automated Let's Encrypt renewal
certbot renew --quiet --deploy-hook "gcloud compute ssl-certificates create ..."
```

### Verification

#### Verify TLS Configuration

```bash
# Check TLS version and cipher suites
openssl s_client -connect yourdomain.com:443 -tls1_2

# Verify HSTS header
curl -I https://yourdomain.com | grep Strict-Transport-Security

# Test with SSL Labs
# Visit: https://www.ssllabs.com/ssltest/analyze.html?d=yourdomain.com
```

#### Expected Results

- **TLS Version**: TLS 1.2 or TLS 1.3
- **HSTS Header**: `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- **HTTP Requests**: Should be rejected with 400 Bad Request
- **SSL Labs Grade**: A or A+

### Monitoring

#### Cloud Monitoring Alerts

Set up alerts for:

1. **Certificate expiration** (30 days before)
   ```bash
   gcloud alpha monitoring policies create \
     --notification-channels=CHANNEL_ID \
     --display-name="SSL Certificate Expiration" \
     --condition-display-name="Certificate expires in 30 days" \
     --condition-threshold-value=30 \
     --condition-threshold-duration=3600s
   ```

2. **TLS handshake failures**
   - Monitor `loadbalancer.googleapis.com/https/request_count` with filter `response_code_class="4xx"`

3. **HTTP requests** (should be rejected)
   - Monitor logs for "HTTPS required" messages

#### Health Checks

```bash
# Automated health check script
#!/bin/bash
DOMAIN="yourdomain.com"

# Check HTTPS is accessible
if ! curl -fs https://$DOMAIN/health > /dev/null; then
  echo "ERROR: HTTPS endpoint not accessible"
  exit 1
fi

# Check HTTP is rejected
if curl -fs http://$DOMAIN/health > /dev/null; then
  echo "ERROR: HTTP should be rejected but was accepted"
  exit 1
fi

# Check HSTS header present
if ! curl -I https://$DOMAIN 2>&1 | grep -q "Strict-Transport-Security"; then
  echo "ERROR: HSTS header not found"
  exit 1
fi

echo "All TLS checks passed"
```

## HIPAA Compliance Checklist

- [x] TLS 1.2+ enforced for all PHI transmission
- [x] HTTP requests rejected in production
- [x] HSTS headers configured (1 year max-age)
- [x] Reverse proxy support (X-Forwarded-Proto)
- [x] Automated certificate management in production
- [x] Certificate expiration monitoring
- [x] Security headers implemented (defense-in-depth)
- [ ] SSL Labs test passes with A or A+ grade
- [ ] Certificate renewal procedures documented and tested
- [ ] Incident response plan for certificate expiration

## Incident Response

### Certificate Expiration Emergency

If a certificate expires unexpectedly:

1. **Immediate action** (Google-managed certs should auto-renew, but if they don't):
   ```bash
   # Force certificate renewal
   gcloud compute ssl-certificates delete OLD_CERT_NAME
   gcloud run domain-mappings create --service=therapy-platform-api --domain=yourdomain.com
   ```

2. **Communication**:
   - Notify users of service interruption
   - Update status page
   - Document root cause

3. **Prevention**:
   - Review monitoring alerts
   - Add redundant certificate expiration checks
   - Consider backup certificate provider

### TLS Vulnerability Response

When a new TLS vulnerability is announced (e.g., Heartbleed, POODLE):

1. **Assess impact**: Check if Google Cloud Platform is affected
2. **Review GCP security bulletins**: https://cloud.google.com/support/bulletins
3. **Update configurations** if needed
4. **Test** with SSL Labs and internal tools
5. **Document** changes in this file

## References

- [HIPAA Security Rule](https://www.hhs.gov/hipaa/for-professionals/security/index.html)
- [NIST Special Publication 800-52 Rev. 2](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-52r2.pdf) - TLS Guidelines
- [OWASP TLS Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Protection_Cheat_Sheet.html)
- [Google Cloud Run HTTPS](https://cloud.google.com/run/docs/configuring/custom-domains)
- [Google-managed SSL certificates](https://cloud.google.com/load-balancing/docs/ssl-certificates/google-managed-certs)
