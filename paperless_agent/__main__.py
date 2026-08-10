"""Allow `python -m paperless_agent` to launch the desktop shell."""

from __future__ import annotations

from paperless_agent.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
