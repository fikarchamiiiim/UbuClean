"""Allow ``python3 -m ubuclean``."""

from __future__ import annotations

from ubuclean.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
