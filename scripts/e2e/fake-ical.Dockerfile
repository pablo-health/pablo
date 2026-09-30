# The stand-in calendar feed for the end-to-end stack (docker-compose.e2e.yml).
# Build context is the repository root so the captured feeds can be copied in
# next to the script, the same shape as the fake NPI registry.
FROM python:3.13-slim

# Same major versions the backend resolves, so the fake and the client it
# stands in for agree about HTTP.
RUN pip install --no-cache-dir "fastapi==0.141.1" "uvicorn==0.51.0"

# The base image has no unprivileged user; the fake needs no privileges.
RUN useradd --create-home --uid 10001 fake

WORKDIR /srv
COPY --chown=fake:fake scripts/fake_ical.py ./
COPY --chown=fake:fake backend/tests/fixtures/simplepractice_feed ./fixtures

USER fake

EXPOSE 8082

CMD ["uvicorn", "fake_ical:app", "--host", "0.0.0.0", "--port", "8082", "--log-level", "warning"]
