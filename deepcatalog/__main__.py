"""Allow `python -m deepcatalog` to launch the desktop shell."""

from __future__ import annotations

from deepcatalog.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
