"""Parse dotenv files without losing their layout.

Most dotenv parsers return a flat ``{key: value}`` dict. That is enough to
load configuration but not enough to *rewrite* a file: ``envdoc sync`` has
to update ``.env.example`` while keeping its comments, blank lines, section
order and the author's quoting. So this parser returns every line, tagged,
and the dict view is derived from that.

Supported syntax (the common subset of vlucas/phpdotenv, python-dotenv and
the Node ``dotenv`` package):

    KEY=value
    export KEY=value
    KEY="double quoted, with \\n escapes"
    KEY='single quoted, literal'
    KEY=value # inline comment (unquoted values only)
    KEY="a value that
    spans lines"
    # full-line comment

Anything else is kept as an ``invalid`` line rather than dropped, so a
rewrite never silently deletes text the user wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

__all__ = ["EnvFile", "EnvLine", "ParseError", "parse", "parse_file"]

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([^=\s]+)\s*=\s*(.*)$", re.DOTALL)

_DQ_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "$": "$"}


class ParseError(ValueError):
    """Raised for files that cannot be read as text at all."""


@dataclass
class EnvLine:
    """One logical line of a dotenv file.

    A multi-line quoted value is a single logical line spanning several
    physical lines; ``lineno`` is where it starts and ``raw`` holds all of
    it, newlines included, so writing ``raw`` back reproduces the file.
    """

    kind: str  # "entry" | "comment" | "blank" | "invalid"
    raw: str
    lineno: int
    key: Optional[str] = None
    value: Optional[str] = None
    quote: str = ""  # "", "'", or '"'
    exported: bool = False
    problem: Optional[str] = None

    @property
    def is_entry(self) -> bool:
        return self.kind == "entry"


@dataclass
class EnvFile:
    """A parsed dotenv file."""

    lines: List[EnvLine] = field(default_factory=list)
    path: Optional[Path] = None

    def entries(self) -> Iterator[EnvLine]:
        return (ln for ln in self.lines if ln.is_entry)

    def keys(self) -> List[str]:
        """Keys in file order, first occurrence only."""
        seen: Dict[str, None] = {}
        for ln in self.entries():
            seen.setdefault(ln.key, None)  # type: ignore[arg-type]
        return list(seen)

    def as_dict(self) -> Dict[str, str]:
        """Last assignment wins, as every dotenv loader does."""
        return {ln.key: ln.value or "" for ln in self.entries()}  # type: ignore[misc]

    def get(self, key: str) -> Optional[EnvLine]:
        """The effective (last) line for ``key``."""
        found = None
        for ln in self.entries():
            if ln.key == key:
                found = ln
        return found

    def duplicates(self) -> Dict[str, List[int]]:
        """Keys assigned more than once, with every line number."""
        where: Dict[str, List[int]] = {}
        for ln in self.entries():
            where.setdefault(ln.key, []).append(ln.lineno)  # type: ignore[arg-type]
        return {k: v for k, v in where.items() if len(v) > 1}

    def invalid(self) -> List[EnvLine]:
        return [ln for ln in self.lines if ln.kind == "invalid"]

    def render(self) -> str:
        """Reassemble the file exactly as parsed."""
        return "".join(ln.raw for ln in self.lines)


def _unescape_double(body: str) -> str:
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body) and body[i + 1] in _DQ_ESCAPES:
            out.append(_DQ_ESCAPES[body[i + 1]])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_closing(text: str, quote: str) -> int:
    """Index of the closing quote in ``text`` (which starts after the
    opening quote), honouring backslash escapes for double quotes."""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote == '"' and ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i
        i += 1
    return -1


def _strip_inline_comment(value: str) -> str:
    # Only " #" starts a comment, so values like URL#fragment survive.
    m = re.search(r"\s+#", value)
    return value[: m.start()] if m else value


def parse(text: str) -> EnvFile:
    """Parse dotenv text into an :class:`EnvFile`."""
    physical = text.splitlines(keepends=True)
    out = EnvFile()
    i = 0
    while i < len(physical):
        raw = physical[i]
        lineno = i + 1
        stripped = raw.strip()

        if not stripped:
            out.lines.append(EnvLine("blank", raw, lineno))
            i += 1
            continue
        if stripped.startswith("#"):
            out.lines.append(EnvLine("comment", raw, lineno))
            i += 1
            continue

        # Match the raw line, newline included: a quoted value that
        # continues onto the next line needs this line's newline in it.
        m = _ASSIGN_RE.match(raw)
        if not m:
            out.lines.append(
                EnvLine("invalid", raw, lineno, problem="no '=' assignment")
            )
            i += 1
            continue

        key, rest = m.group(1), m.group(2)
        exported = raw.lstrip().startswith("export ")

        if not _KEY_RE.match(key):
            out.lines.append(
                EnvLine("invalid", raw, lineno, key=key, problem=f"invalid key {key!r}")
            )
            i += 1
            continue

        quote = rest[:1] if rest[:1] in ("'", '"') else ""
        if quote:
            # Accumulate physical lines until the quote closes.
            body = rest[1:]
            consumed = [raw]
            j = i
            close = _find_closing(body, quote)
            while close == -1 and j + 1 < len(physical):
                j += 1
                consumed.append(physical[j])
                body += physical[j]
                close = _find_closing(body, quote)

            full_raw = "".join(consumed)
            if close == -1:
                out.lines.append(
                    EnvLine(
                        "invalid",
                        full_raw,
                        lineno,
                        key=key,
                        problem=f"unterminated {quote} quote",
                    )
                )
                i = j + 1
                continue

            inner = body[:close].replace("\r\n", "\n")
            value = _unescape_double(inner) if quote == '"' else inner
            out.lines.append(
                EnvLine(
                    "entry",
                    full_raw,
                    lineno,
                    key=key,
                    value=value,
                    quote=quote,
                    exported=exported,
                )
            )
            i = j + 1
            continue

        value = _strip_inline_comment(rest).strip()
        out.lines.append(
            EnvLine("entry", raw, lineno, key=key, value=value, exported=exported)
        )
        i += 1

    return out


def parse_file(path: str | Path) -> EnvFile:
    """Read and parse a dotenv file.

    Decodes as UTF-8, tolerating a BOM - Windows editors add one, and an
    unstripped BOM would glue itself to the first key's name.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(f"{path} is not UTF-8 text") from exc
    env = parse(text)
    env.path = path
    return env
