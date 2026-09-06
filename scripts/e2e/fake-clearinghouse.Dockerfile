# The fake clearinghouse for the end-to-end stack (docker-compose.e2e.yml).
# Build context is the repository root so the recorded vendor responses can
# be copied in next to the script.
FROM python:3.13-slim

# Same major versions the backend resolves, so the fake and the adapter it
# stands in for parse JSON the same way.
RUN pip install --no-cache-dir "fastapi==0.141.1" "uvicorn==0.51.0" "httpx==0.28.1"

WORKDIR /srv
COPY scripts/fake_clearinghouse.py ./
COPY backend/tests/fixtures/clearinghouse ./fixtures

EXPOSE 8080

CMD ["uvicorn", "fake_clearinghouse:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
