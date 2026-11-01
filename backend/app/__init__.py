"""
Pablo backend application.
"""

import os
from pathlib import Path

__version__ = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()


def release_version() -> str:
    """The release this process runs: ``PABLO_VERSION``, else the packaged ``VERSION``.

    A deployment sets ``PABLO_VERSION`` to the image tag it pulled (``v1.4.0``);
    a build run without one reports the version in the source tree.
    """
    return os.environ.get("PABLO_VERSION") or __version__
