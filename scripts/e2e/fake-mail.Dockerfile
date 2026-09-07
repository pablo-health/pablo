# The fake mail server for the end-to-end stack (docker-compose.e2e.yml).
# Build context is the repository root, matching the fake clearinghouse.
FROM python:3.13-slim

# Same major versions the backend resolves for the shared ones, so the fake
# and the sender it stands in for agree about TLS and JSON.
RUN pip install --no-cache-dir \
    "fastapi==0.141.1" \
    "uvicorn==0.51.0" \
    "aiosmtpd==1.4.6" \
    "cryptography==46.0.3"

# The base image has no unprivileged user; the fake needs no privileges.
RUN useradd --create-home --uid 10001 fake

WORKDIR /srv
COPY --chown=fake:fake scripts/fake_mail.py ./
RUN mkdir -p /srv/tls && chown fake:fake /srv/tls

USER fake

EXPOSE 1025 8025

CMD ["uvicorn", "fake_mail:app", "--host", "0.0.0.0", "--port", "8025", "--log-level", "warning"]
