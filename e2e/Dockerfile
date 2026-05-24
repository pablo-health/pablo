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

RUN chmod +x scripts/*.sh

ENTRYPOINT ["scripts/entrypoint.sh"]
