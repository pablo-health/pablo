"""Cloud Run migrate-job entrypoint — chdir into backend/ then run alembic.

The production image WORKDIR is /app; alembic.ini and the migrations tree
live at /app/backend/. Alembic's `prepend_sys_path = .` resolves against
the working directory, so we chdir before delegating to the CLI.

Default args = `upgrade head`. Override by passing args to the Cloud Run
job (e.g. ``--args=backend/bin/migrate.py,downgrade,-1``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    from alembic.config import main

    sys.exit(main(argv=sys.argv[1:] or ["upgrade", "head"]))
