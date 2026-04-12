# Frontend Dockerfile for local development
# Uses Node.js 22 LTS for Next.js 15

FROM node:22-slim

WORKDIR /app

# Install dependencies first (for better caching)
COPY package.json package-lock.json ./

RUN npm ci

# Copy application code
COPY --chown=node:node . .

USER node

# Expose port
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000', (r) => r.statusCode === 200 ? process.exit(0) : process.exit(1))"

# Run in development mode with hot reload
CMD ["npm", "run", "dev"]
