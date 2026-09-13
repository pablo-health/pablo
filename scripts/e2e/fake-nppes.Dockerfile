# The fake NPI registry for the end-to-end stack (docker-compose.e2e.yml).
# Build context is the repository root so the fixture records can be copied in
# next to the script, the same shape as the fake clearinghouse.
FROM python:3.13-slim

# Same major versions the backend resolves, so the fake and the client it
# stands in for agree about JSON.
RUN pip install --no-cache-dir "fastapi==0.141.1" "uvicorn==0.51.0"

# The base image has no unprivileged user; the fake needs no privileges.
RUN useradd --create-home --uid 10001 fake

WORKDIR /srv
COPY --chown=fake:fake scripts/fake_nppes.py ./
COPY --chown=fake:fake backend/tests/fixtures/nppes ./fixtures

USER fake

EXPOSE 8081

CMD ["uvicorn", "fake_nppes:app", "--host", "0.0.0.0", "--port", "8081", "--log-level", "warning"]
