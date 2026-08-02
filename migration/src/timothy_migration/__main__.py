"""Entry point for `timothy-migrate`."""

from __future__ import annotations

import sys

from timothy_migration.cli import main

if __name__ == "__main__":  # pragma: no cover — exercised by running the script
    sys.exit(main())
