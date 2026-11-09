"""Allow `python -m automatejob ...`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
