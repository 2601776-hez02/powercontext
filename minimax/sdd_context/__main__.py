"""Allow ``python -m sdd_context``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
