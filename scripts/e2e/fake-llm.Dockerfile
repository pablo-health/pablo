# The stand-in for the availability-parsing model in the end-to-end stack
# (docker-compose.e2e.yml). Build context is the repository root, the same
# shape as the other fakes.
FROM python:3.13-slim

# Same major versions the backend resolves, so the fake and the client it
# stands in for agree about HTTP.
RUN pip install --no-cache-dir "fastapi==0.141.1" "uvicorn==0.51.0"

# The base image has no unprivileged user; the fake needs no privileges.
RUN useradd --create-home --uid 10001 fake

WORKDIR /srv
COPY --chown=fake:fake scripts/fake_llm.py ./

USER fake

EXPOSE 8083

CMD ["uvicorn", "fake_llm:app", "--host", "0.0.0.0", "--port", "8083", "--log-level", "warning"]
