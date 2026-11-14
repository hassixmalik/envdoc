"""Small file helpers shared by commands."""

from pathlib import Path

__all__ = ["write_exact"]


def write_exact(path: Path, text: str) -> None:
    """Write text with no newline translation, so CRLF/LF stays as given.

    ``Path.write_text(newline=...)`` only exists on Python 3.10+, and envdoc
    supports 3.9, so open the file directly.
    """
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
