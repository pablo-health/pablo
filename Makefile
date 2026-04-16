.PHONY: help install lint format test test-integration test-all check clean
.PHONY: docker-up docker-down docker-restart docker-logs docker-shell-backend docker-shell-frontend
.PHONY: docker-test-backend docker-test-frontend docker-lint-backend docker-lint-frontend docker-check
.PHONY: docker-clean docker-rebuild docker-status

# Default target
help:
	@echo "Available commands:"
	@echo ""
	@echo "Local Development (Poetry):"
	@echo "  make install           - Install dependencies with Poetry"
	@echo "  make lint              - Run linters (ruff + mypy)"
	@echo "  make format            - Auto-format code with ruff"
	@echo "  make test              - Run unit tests with coverage"
	@echo "  make test-integration  - Run integration tests (requires Firestore emulator)"
	@echo "  make test-all          - Run both unit and integration tests"
	@echo "  make check             - Run lint + test (CI-style)"
	@echo "  make clean             - Clean generated files"
	@echo ""
	@echo "Docker Development:"
	@echo "  make docker-up         - Start all Docker services"
	@echo "  make docker-down       - Stop all Docker services"
	@echo "  make docker-restart    - Restart all Docker services"
	@echo "  make docker-logs       - View logs from all services"
	@echo "  make docker-shell-backend  - Open shell in backend container"
	@echo "  make docker-shell-frontend - Open shell in frontend container"
	@echo "  make docker-test-backend   - Run backend tests in Docker"
	@echo "  make docker-test-frontend  - Run frontend tests in Docker"
	@echo "  make docker-lint-backend   - Run backend linting in Docker"
	@echo "  make docker-lint-frontend  - Run frontend linting in Docker"
	@echo "  make docker-check      - Run lint + test in Docker"
	@echo "  make docker-clean      - Clean up Docker containers and volumes"
	@echo "  make docker-rebuild    - Rebuild Docker images without cache"
	@echo "  make docker-status     - Show status of Docker containers"

# Install dependencies
install:
	poetry install

# Run linters
lint:
	@echo "Running Ruff linter..."
	poetry run ruff check backend/
	@echo "Running mypy type checker..."
	cd backend && poetry run mypy app

# Auto-format code
format:
	@echo "Formatting code with Ruff..."
	poetry run ruff format backend/
	poetry run ruff check --fix backend/

# Run unit tests with coverage
test:
	@echo "Running unit tests with coverage..."
	cd backend && poetry run pytest tests/ --cov=app --cov-report=term-missing --cov-report=html

# Run integration tests (requires Firestore emulator)
test-integration:
	@echo "Running integration tests..."
	@if [ -z "$$FIRESTORE_EMULATOR_HOST" ]; then \
		echo "Error: FIRESTORE_EMULATOR_HOST not set"; \
		echo "Start emulator with: firebase emulators:start --only firestore"; \
		echo "Then set: export FIRESTORE_EMULATOR_HOST=localhost:8080"; \
		exit 1; \
	fi
	cd backend && poetry run pytest tests_integration/ -v

test-integration-tenant:
	@echo "Running multi-tenant isolation tests (requires Postgres)..."
	cd backend && DATABASE_BACKEND=postgres MULTI_TENANCY_ENABLED=true \
		poetry run pytest tests_integration/database/test_tenant_isolation.py -v

# Run all tests (unit + integration)
test-all:
	@echo "Running all tests..."
	@if [ -z "$$FIRESTORE_EMULATOR_HOST" ]; then \
		echo "Error: FIRESTORE_EMULATOR_HOST not set"; \
		echo "Start emulator with: firebase emulators:start --only firestore"; \
		echo "Then set: export FIRESTORE_EMULATOR_HOST=localhost:8080"; \
		exit 1; \
	fi
	cd backend && poetry run pytest tests/ tests_integration/ --cov=app --cov-report=term-missing --cov-report=html

# Run all checks (lint + test)
check: lint test
	@echo "All checks passed!"

# Clean generated files
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage

# ============================================================================
# PostgreSQL Database Commands
# ============================================================================

# Start just the PostgreSQL container
db-up:
	docker compose up -d postgres

# Stop PostgreSQL container
db-down:
	docker compose stop postgres

# Run Alembic migrations (practice schema)
db-migrate:
	cd backend && DATABASE_BACKEND=postgres DATABASE_URL=postgresql://pablo:pablo_dev@localhost:5432/pablo poetry run alembic upgrade head

# Generate a new Alembic migration
db-revision:
	@read -p "Migration message: " msg && \
	cd backend && DATABASE_BACKEND=postgres DATABASE_URL=postgresql://pablo:pablo_dev@localhost:5432/pablo poetry run alembic revision --autogenerate -m "$$msg"

# Reset database (drop and recreate all schemas)
db-reset:
	@echo "Dropping and recreating database..."
	docker compose exec postgres psql -U pablo -d pablo -c "DROP SCHEMA IF EXISTS practice CASCADE; DROP SCHEMA IF EXISTS platform CASCADE;"
	$(MAKE) db-migrate
	@echo "Database reset complete."

# Connect to local PostgreSQL via psql
db-shell:
	docker compose exec postgres psql -U pablo -d pablo

# Connect to dev Cloud SQL via psql (requires proxy running)
db-dev-shell:
	PGPASSWORD=$$PABLO_DEV_DB_PASSWORD psql -h localhost -p 5433 -U pablo -d pablo

# Show PostgreSQL logs
db-logs:
	docker compose logs -f postgres

# Start Cloud SQL Auth Proxy for dev (connects localhost:5433 → Cloud SQL dev)
db-dev-proxy:
	@echo "Starting Cloud SQL Auth Proxy for pablohealth-dev..."
	@echo "Connect with: DATABASE_URL=postgresql://pablo:PASSWORD@localhost:5433/pablo"
	cloud-sql-proxy pablohealth-dev:us-central1:pablo-dev --port 5433

# ============================================================================
# Docker Development Commands
# ============================================================================

# Start all Docker services
docker-up:
	docker compose up -d

# Stop all Docker services
docker-down:
	docker compose down

# Restart all Docker services
docker-restart:
	docker compose restart

# View logs from all services
docker-logs:
	docker compose logs -f

# View logs for specific service
docker-logs-frontend:
	docker compose logs -f frontend

docker-logs-backend:
	docker compose logs -f backend

docker-logs-firebase:
	docker compose logs -f firebase-emulators

# Open shell in backend container
docker-shell-backend:
	docker compose exec backend /bin/bash

# Open shell in frontend container
docker-shell-frontend:
	docker compose exec frontend /bin/sh

# Run backend tests in Docker
docker-test-backend:
	docker compose exec backend pytest

# Run frontend tests in Docker
docker-test-frontend:
	docker compose exec frontend npm test

# Run backend linting in Docker
docker-lint-backend:
	docker compose exec backend ruff check .
	docker compose exec backend mypy .

# Run frontend linting in Docker
docker-lint-frontend:
	docker compose exec frontend npm run lint

# Format backend code in Docker
docker-format-backend:
	docker compose exec backend ruff format .

# Format frontend code in Docker
docker-format-frontend:
	docker compose exec frontend npm run format

# Run all checks in Docker (lint + test)
docker-check: docker-lint-backend docker-lint-frontend docker-test-backend docker-test-frontend
	@echo "All Docker checks passed!"

# Clean up Docker containers and volumes
docker-clean:
	docker compose down -v
	docker system prune -f

# Rebuild Docker images without cache
docker-rebuild:
	docker compose build --no-cache

# Show status of Docker containers
docker-status:
	docker compose ps
