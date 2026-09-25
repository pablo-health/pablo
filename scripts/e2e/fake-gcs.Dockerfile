# The fake Google Cloud Storage server for the end-to-end stack
# (docker-compose.e2e.yml), and the one-shot that mints the key it verifies
# against. Build context is the repository root, matching the other fakes.
FROM python:3.13-slim

# Same versions the backend resolves: cryptography checks the signatures the
# backend's copy made, and google-crc32c computes the checksum its storage
# client validates every upload and download against.
RUN pip install --no-cache-dir \
    "fastapi==0.141.1" "uvicorn==0.51.0" "cryptography==50.0.1" "google-crc32c==1.8.0"

# The base image has no unprivileged user; the fake needs no privileges.
RUN useradd --create-home --uid 10001 fake

# Owned by the fake so the named volume mounted here starts out writable by
# the minting one-shot, which runs as the same user.
RUN mkdir -p /srv/gcs-credentials && chown fake:fake /srv/gcs-credentials

WORKDIR /srv
COPY --chown=fake:fake scripts/e2e/fake_gcs.py scripts/e2e/fake_gcs_signing.py ./

USER fake

EXPOSE 9000

CMD ["uvicorn", "--factory", "fake_gcs:app_from_env", "--host", "0.0.0.0", "--port", "9000", "--log-level", "warning"]
