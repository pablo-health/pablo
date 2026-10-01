# The fake DNS server for the end-to-end stack (docker-compose.e2e.yml). Build
# context is the repository root, matching the other fakes.
FROM python:3.13-slim

# Same major versions the backend resolves.
RUN pip install --no-cache-dir "fastapi==0.141.1" "uvicorn==0.51.0" "dnspython==2.8.0"

# The base image has no unprivileged user; the fake needs no privileges.
RUN useradd --create-home --uid 10001 fake

WORKDIR /srv
COPY --chown=fake:fake scripts/fake_dns.py ./

USER fake

# HTTP for the /_fake hooks; DNS over UDP on 5353.
EXPOSE 8027 5353/udp

CMD ["uvicorn", "fake_dns:app", "--host", "0.0.0.0", "--port", "8027", "--log-level", "warning"]
