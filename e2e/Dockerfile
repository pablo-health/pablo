#
# Playwright E2E test runner for pablo OSS.
# Runs as a Cloud Run Job in pablohealth-oss (us-central1).
# See e2e/README.md.
#
# Pin Playwright to a specific version — browser versions drift and
# silently break tests. Bump deliberately, not via :latest.
#
FROM mcr.microsoft.com/playwright:v1.55.1-noble

ENV CI=true \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json playwright.config.ts ./
COPY fixtures ./fixtures
COPY specs ./specs
COPY scripts ./scripts

RUN chmod +x scripts/*.sh && chown -R pwuser:pwuser /app

# Run as the non-root user shipped in the Playwright base image rather
# than root (Trivy DS-0002). pwuser owns /app so the runner can still
# write test-results/ and the HTML report.
USER pwuser

ENTRYPOINT ["scripts/entrypoint.sh"]
