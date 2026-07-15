# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Keyless Firebase Admin credentials via Workload Identity Federation.

Hosts without Application Default Credentials (e.g. running on AWS, not GCP)
can't use `credentials.ApplicationDefault()` — it reaches the GCP metadata
server, which isn't there. This builds a firebase-admin credential backed by
Workload Identity Federation: the runtime's AWS role is federated to
impersonate a GCP service account, so both token verification *and*
custom-token signing (passkeys) work — with no static service-account key.

Python's ``google.auth`` supports external-account (WIF) credentials natively.
Two wrinkles handled here:

- **AWS credential source.** google.auth's built-in AWS supplier reads the EC2
  IMDS, but ECS/Fargate serves task-role credentials from the container
  credentials endpoint. A custom ``AwsSecurityCredentialsSupplier`` reads that
  endpoint using google.auth's own transport (no extra dependency — GCP images
  deliberately don't ship boto3).
- **Custom-token signing.** ``external_account.Credentials`` is not a
  ``credentials.Signing`` type, so firebase-admin can't sign custom tokens from
  it directly. Callers must pass ``serviceAccountId`` to ``initialize_app`` (we
  expose :pyattr:`service_account_email`) — that routes signing through IAM
  ``signBlob`` using this credential, which is why the impersonated SA needs
  ``iam.serviceAccounts.signBlob`` on itself.
"""

from __future__ import annotations

import json
import os
import re
from http import HTTPStatus
from typing import Any

import google.auth.aws
from firebase_admin import credentials
from google.auth import exceptions as google_auth_exceptions

_STS_TOKEN_URL = "https://sts.googleapis.com/v1/token"  # noqa: S105 (URL, not a secret)
_AWS_SUBJECT_TOKEN_TYPE = "urn:ietf:params:aws:token-type:aws4_request"  # noqa: S105
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
# ECS/Fargate task-role credentials endpoint (link-local; no IMDS on Fargate).
_ECS_CREDS_HOST = "http://169.254.170.2"

# The impersonation URL is used verbatim as the IAM endpoint the federated token
# is POSTed to; pin it to the real iamcredentials host + extract the SA email.
_IMPERSONATION_RE = re.compile(
    r"^https://iamcredentials\.googleapis\.com/v1/projects/-/serviceAccounts/"
    r"(?P<email>[^/:]+@[^/:]+\.iam\.gserviceaccount\.com):generateAccessToken$"
)
_AUDIENCE_RE = re.compile(
    r"^//iam\.googleapis\.com/projects/\d+/locations/global/"
    r"workloadIdentityPools/[^/]+/providers/[^/]+$"
)


class _EcsAwsSupplier(google.auth.aws.AwsSecurityCredentialsSupplier):
    """Supplies AWS creds from the ECS/Fargate task-role endpoint.

    google.auth's default AWS supplier only reads the EC2 IMDS; on Fargate the
    task-role credentials live at the ECS container-credentials endpoint
    (``$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`` / ``_FULL_URI``). We fetch it
    with google.auth's own request transport so there's no boto3 dependency
    (GCP images don't ship it). The endpoint rotates creds; google.auth caches
    the derived federated token and only calls this on refresh (~hourly).
    """

    def _creds_url(self) -> str:
        full = os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        rel = os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        if full:
            return full
        if rel:
            return _ECS_CREDS_HOST + rel
        raise google_auth_exceptions.RefreshError(
            "no ECS container-credentials URI — is the ECS task role attached?"
        )

    def get_aws_security_credentials(
        self, _context: Any, request: Any
    ) -> google.auth.aws.AwsSecurityCredentials:
        headers = {}
        token = os.environ.get("AWS_CONTAINER_AUTHORIZATION_TOKEN")
        if token:
            headers["Authorization"] = token
        response = request(url=self._creds_url(), method="GET", headers=headers)
        if response.status != HTTPStatus.OK:
            raise google_auth_exceptions.RefreshError(
                f"ECS credentials endpoint returned {response.status}"
            )
        data = json.loads(response.data)
        return google.auth.aws.AwsSecurityCredentials(
            data["AccessKeyId"], data["SecretAccessKey"], data.get("Token")
        )

    def get_aws_region(self, _context: Any, _request: Any) -> str:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not region:
            raise google_auth_exceptions.RefreshError(
                "cannot determine AWS region (set AWS_REGION)"
            )
        return region


class WorkloadIdentityCredential(credentials.Base):
    """firebase-admin credential wrapping a WIF (external-account) google.auth credential.

    ``get_credential()`` returns the impersonated-SA credential; firebase-admin
    uses it for id-token verification, and — when ``serviceAccountId`` is passed
    to ``initialize_app`` (see :pyattr:`service_account_email`) — for
    custom-token signing via IAM ``signBlob``.
    """

    def __init__(self, *, audience: str, sa_impersonation_url: str, project_id: str) -> None:
        if not _AUDIENCE_RE.match(audience or ""):
            raise ValueError(
                "firebase_wif_audience must look like "
                "//iam.googleapis.com/projects/<num>/locations/global/"
                "workloadIdentityPools/<pool>/providers/<provider>"
            )
        m = _IMPERSONATION_RE.match(sa_impersonation_url or "")
        if not m:
            raise ValueError(
                "firebase_wif_sa_impersonation_url must be the iamcredentials "
                "generateAccessToken URL for a service account"
            )
        self._project_id = project_id
        self._service_account_email = m.group("email")
        self._google_cred = google.auth.aws.Credentials(
            audience=audience,
            subject_token_type=_AWS_SUBJECT_TOKEN_TYPE,
            token_url=_STS_TOKEN_URL,
            service_account_impersonation_url=sa_impersonation_url,
            aws_security_credentials_supplier=_EcsAwsSupplier(),
            scopes=_SCOPES,
        )

    def get_credential(self) -> google.auth.aws.Credentials:
        return self._google_cred

    @property
    def service_account_email(self) -> str:
        """The impersonated SA — pass as ``serviceAccountId`` so firebase-admin
        signs custom tokens via IAM ``signBlob`` (the passkey path)."""
        return self._service_account_email

    @property
    def project_id(self) -> str:
        return self._project_id
