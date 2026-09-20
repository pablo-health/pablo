# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Create the bucket the end-to-end stack's object store serves from.

The stack runs an S3-compatible store so a document upload can be proven
the way a person performs it: the browser sends the file to the store
directly, against a URL the API signed, and the API later reads the object
back. A real deployment provisions its bucket out of band; this stack has
no out of band, so one script does it on the way up.

Doubles as the store's readiness signal. The store is up when a bucket can
be made in it, which is a stronger claim than a port being open and needs
nothing installed in the store's own image -- so compose waits on this
having exited cleanly rather than on a health probe.

Idempotent: an existing bucket is a success, so restarting the stack
without dropping its volumes is safe.

Usage (from the end-to-end compose stack)::

    python -m backend.scripts.e2e_create_object_store_bucket
"""

from __future__ import annotations

import logging
import os
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("e2e-object-store")

# The store is starting in parallel with this container, so the first few
# calls are expected to fail. Generous enough for a cold image pull to
# finish unpacking, short enough that a genuinely broken store fails the
# stack rather than hanging it.
_ATTEMPTS = 60
_DELAY_SECONDS = 2.0


def main() -> int:
    endpoint_url = os.environ["E2E_OBJECT_STORE_ENDPOINT"]
    bucket = os.environ["E2E_OBJECT_STORE_BUCKET"]
    region = os.environ.get("AWS_REGION", "us-east-1")

    client = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        config=Config(signature_version="s3v4", retries={"max_attempts": 1}),
    )

    last_error: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            # Both spellings mean the same thing across S3 implementations,
            # and both mean this script has nothing left to do.
            if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                logger.info("object store bucket already present: %s", bucket)
                return 0
            last_error = exc
        except (BotoCoreError, OSError) as exc:
            last_error = exc
        else:
            logger.info("object store bucket created: %s", bucket)
            return 0

        if attempt < _ATTEMPTS:
            time.sleep(_DELAY_SECONDS)

    logger.error("object store never accepted a bucket at %s: %s", endpoint_url, last_error)
    return 1


if __name__ == "__main__":
    sys.exit(main())
