# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Keep a test run away from the Google credentials of the machine it runs on.

CI has no Application Default Credentials and no gcloud configuration, so a
code path a test forgot to stub — a best-effort model call behind a route,
say — fails quietly there. A workstation logged in with ``gcloud auth
application-default login`` is different: ``google.auth.default()`` finds
those credentials, picks up gcloud's default project, and the same forgotten
call becomes a real, billed Vertex AI request against that project. The test
still passes, so nothing points at it.

``isolate_from_ambient_gcp_credentials`` gives every run the environment CI
already has. It must run before anything imports ``google.auth``:
``NO_GCE_CHECK`` is read once, at import, and it is what spares each
credential lookup a multi-second metadata-server probe. So importing this
module applies it, and both conftests import it ahead of everything else::

    import tests.ambient_credentials  # noqa: F401  (first import)
"""

from __future__ import annotations

import os
import tempfile


def isolate_from_ambient_gcp_credentials() -> None:
    """Point gcloud and ADC discovery at nothing, as CI does.

    * ``CLOUDSDK_CONFIG`` → an empty directory, so neither the ADC file nor
      gcloud's default project is found.
    * ``GOOGLE_APPLICATION_CREDENTIALS`` removed, so an exported key file is
      not used either.
    * ``NO_GCE_CHECK=true``, so the lookup does not probe for a metadata
      server and fails in milliseconds instead of seconds.

    Tests that exercise credential handling mock ``google.auth.default``
    themselves and are unaffected.
    """
    os.environ["CLOUDSDK_CONFIG"] = tempfile.mkdtemp(prefix="pablo-tests-gcloud-")
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    os.environ["NO_GCE_CHECK"] = "true"


isolate_from_ambient_gcp_credentials()
