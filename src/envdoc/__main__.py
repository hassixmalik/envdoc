"""Allow ``python -m envdoc``; the hook script falls back to this."""

from .cli import main

raise SystemExit(main())
