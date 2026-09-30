# The stand-in Google (OAuth + Calendar v3) for the end-to-end stack
# (docker-compose.e2e.yml). Build context is the repository root, matching
# the other fakes.
FROM python:3.13-slim

# Same versions the backend resolves: dateutil expands a recurring series
# here by the same rules the backend reads one with.
RUN pip install --no-cache-dir \
    "fastapi==0.141.1" "uvicorn==0.51.0" "python-multipart==0.0.32" "python-dateutil==2.9.0.post0"

# The base image has no unprivileged user; the fake needs no privileges.
RUN useradd --create-home --uid 10001 fake

WORKDIR /srv
COPY --chown=fake:fake scripts/e2e/fake_google.py ./

USER fake

EXPOSE 8090

CMD ["uvicorn", "fake_google:app", "--host", "0.0.0.0", "--port", "8090", "--log-level", "warning"]
