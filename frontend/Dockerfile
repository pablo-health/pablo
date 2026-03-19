# Frontend Dockerfile for local development
# Uses Node.js 22 LTS for Next.js 15

FROM node:22-slim

WORKDIR /app

# Install dependencies first (for better caching)
COPY package.json package-lock.json ./

RUN npm ci

# Copy application code
COPY . .

# Expose port
EXPOSE 3000

# Run in development mode with hot reload
CMD ["npm", "run", "dev"]
