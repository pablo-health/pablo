.PHONY: help lint format test check run clean

help:
	@echo "Available targets:"
	@echo "  make lint    - Run linting (ruff + mypy)"
	@echo "  make format  - Auto-format code with ruff"
	@echo "  make test    - Run tests with pytest"
	@echo "  make check   - Run lint + test (CI workflow)"
	@echo "  make run     - Run the development server"
	@echo "  make clean   - Remove cache files"

lint:
	@echo "Running ruff linter..."
	poetry run ruff check .
	@echo "Running mypy type checker..."
	poetry run mypy app tests

format:
	@echo "Formatting code with ruff..."
	poetry run ruff check --fix .
	poetry run ruff format .

test:
	@echo "Running tests with pytest..."
	poetry run pytest tests/ -v --cov=app --cov-report=html --cov-report=term

check: lint test
	@echo "✓ All checks passed!"

run:
	@echo "Starting development server..."
	poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

clean:
	@echo "Cleaning cache files..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf htmlcov/ .coverage
	@echo "✓ Cleaned!"
