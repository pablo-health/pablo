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
  credentials endpoint. We bridge that with boto3 (a custom
  ``AwsSecurityCredentialsSupplier``), which resolves + rotates ECS/Fargate
  creds natively.
- **Custom-token signing.** ``external_account.Credentials`` is not a
  ``credentials.Signing`` type, so firebase-admin can't sign custom tokens from
  it directly. Callers must pass ``serviceAccountId`` to ``initialize_app`` (we
  expose :pyattr:`service_account_email`) — that routes signing through IAM
  ``signBlob`` using this credential, which is why the impersonated SA needs
  ``iam.serviceAccounts.signBlob`` on itself.
"""

from __future__ import annotations

import os
import re
from typing import Any

import boto3
import google.auth.aws
from firebase_admin import credentials
from google.auth import exceptions as google_auth_exceptions

_STS_TOKEN_URL = "https://sts.googleapis.com/v1/token"  # noqa: S105 (URL, not a secret)
_AWS_SUBJECT_TOKEN_TYPE = "urn:ietf:params:aws:token-type:aws4_request"  # noqa: S105
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

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
    """Supplies AWS creds from the ECS/Fargate task-role endpoint via boto3.

    google.auth's default AWS supplier only reads the EC2 IMDS; on Fargate the
    task-role credentials live at the ECS container-credentials endpoint, which
    boto3 resolves natively (and rotates automatically).
    """

    def __init__(self) -> None:
        self._session = boto3.Session()

    def get_aws_security_credentials(
        self, _context: Any, _request: Any
    ) -> google.auth.aws.AwsSecurityCredentials:
        creds = self._session.get_credentials()
        if creds is None:
            # Fail closed with a diagnosable error instead of an opaque
            # AttributeError deep inside a token refresh.
            raise google_auth_exceptions.RefreshError(
                "no AWS credentials resolved — is the ECS task role attached?"
            )
        frozen = creds.get_frozen_credentials()
        return google.auth.aws.AwsSecurityCredentials(
            frozen.access_key, frozen.secret_key, frozen.token
        )

    def get_aws_region(self, _context: Any, _request: Any) -> str:
        region = self._session.region_name or os.environ.get("AWS_REGION", "")
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
