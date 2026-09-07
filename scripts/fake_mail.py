# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in mail server for the end-to-end stack.

The product's only SMTP backend (``app.services.email_sender.SmtpEmailSender``)
negotiates STARTTLS against the platform's default certificate store and then
authenticates, so a plain mail catcher is not enough: the stack needs a server
that offers a certificate the backend trusts. This process is that server. It

* mints a self-signed certificate for its own service name at startup and
  writes it to a directory the backend mounts, so the backend can trust it by
  pointing ``SSL_CERT_FILE`` at the file — nothing else in the stack speaks
  TLS, so lending the whole trust store to this one certificate costs nothing;
* accepts SMTP on port 1025 with STARTTLS required and any username and
  password;
* keeps every message it receives in memory, readable over HTTP.

Test hooks live under ``/_fake``: ``GET /_fake/messages`` lists what has been
received since the last reset, newest last, and ``POST /_fake/reset`` clears
the list. Neither the SMTP nor the HTTP side ever reaches the network.

Configuration is by environment: ``FAKE_MAIL_HOSTNAME`` (the name the
certificate is issued for — it must match the host the backend connects to),
``FAKE_MAIL_TLS_DIR`` (where the certificate and key are written) and
``FAKE_MAIL_SMTP_PORT``. The HTTP port is uvicorn's own argument.

Run locally with ``uvicorn scripts.fake_mail:app --port 8025``; the compose
stack builds it from ``scripts/e2e/fake-mail.Dockerfile``.
"""

from __future__ import annotations

import email
import os
import ssl
from datetime import UTC, datetime, timedelta
from email import policy
from pathlib import Path
from typing import Any

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP, AuthResult, Envelope, Session
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI

HOSTNAME = os.environ.get("FAKE_MAIL_HOSTNAME", "fake-mail")
TLS_DIR = Path(os.environ.get("FAKE_MAIL_TLS_DIR", "/srv/tls"))
SMTP_PORT = int(os.environ.get("FAKE_MAIL_SMTP_PORT", "1025"))

#: The certificate is minted per start and matters to nobody afterwards, but
#: a stack can be left up for a long time; a decade means it never expires
#: mid-run and fails somewhere far from the cause.
_CERT_DAYS = 3650


def _write_self_signed_cert() -> tuple[Path, Path]:
    """Mint a certificate for :data:`HOSTNAME` and return ``(cert, key)`` paths.

    Regenerated every start rather than committed: a private key in a public
    repository is a bad habit to teach even when it guards nothing, and a
    certificate minted at startup has no expiry to trip over.
    """
    cert_path = TLS_DIR / "cert.pem"
    key_path = TLS_DIR / "key.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTNAME)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=_CERT_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOSTNAME)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    TLS_DIR.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # The backend mounts this directory and reads the certificate as another
    # user; the key stays readable only because the two are written together.
    cert_path.chmod(0o644)
    key_path.chmod(0o600)
    return cert_path, key_path


class _Mailbox:
    """Everything a test can observe or reset."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def add(self, sender: str, recipients: list[str], raw: bytes) -> None:
        parsed = email.message_from_bytes(raw, policy=policy.default)
        body = parsed.get_body(preferencelist=("plain",))
        self.messages.append(
            {
                "at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "from": sender,
                "to": recipients,
                "subject": str(parsed["Subject"] or ""),
                "text": body.get_content() if body is not None else "",
            }
        )

    def reset(self) -> None:
        self.messages.clear()


mailbox = _Mailbox()


class _Handler:
    """Keeps whatever arrives; refuses nothing."""

    async def handle_DATA(self, _server: SMTP, _session: Session, envelope: Envelope) -> str:  # noqa: N802 — aiosmtpd's hook name
        mailbox.add(
            envelope.mail_from or "",
            list(envelope.rcpt_tos),
            envelope.original_content or b"",
        )
        return "250 Message accepted for delivery"


def _accept_any_login(
    _server: SMTP,
    _session: Session,
    _envelope: Envelope,
    _mechanism: str,
    _auth_data: object,
) -> AuthResult:
    """Any username and password. The stack holds no mail credentials."""
    return AuthResult(success=True)


def _start_smtp() -> Controller:
    cert_path, key_path = _write_self_signed_cert()
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(str(cert_path), str(key_path))
    controller = Controller(
        _Handler(),
        hostname="0.0.0.0",  # noqa: S104 — a container-local listener on the stack's network
        port=SMTP_PORT,
        server_hostname=HOSTNAME,
        tls_context=tls_context,
        require_starttls=True,
        authenticator=_accept_any_login,
    )
    controller.start()
    return controller


app = FastAPI(title="fake mail", docs_url=None, redoc_url=None)
_controller = _start_smtp()


@app.get("/_fake/messages")
async def messages() -> Any:
    return {"messages": mailbox.messages}


@app.post("/_fake/reset")
async def reset() -> Any:
    mailbox.reset()
    return {"ok": True}
