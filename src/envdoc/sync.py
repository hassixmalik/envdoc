"""Bring ``.env.example`` up to date with ``.env``.

Rules, in order of how much they matter:

1. **Never copy a secret.** A value is carried across only when
   :func:`publishable` says it is generic (``local``, ``3306``,
   ``127.0.0.1``). Everything else becomes ``KEY=``.
2. **Never lose what the author wrote.** Comments, blank lines, ordering,
   quoting and unparseable lines in the example are kept as they are.
3. **Put new keys where a human would.** A new ``DB_SSLMODE`` goes after
   the last ``DB_*`` key already in the example, not at the bottom of the
   file. Keys with no family in the example are appended under a marker
   comment so the change is easy to review.

Removing keys from the example (``prune``) is opt-in: an example often
documents optional settings the current developer simply has not set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .classify import publishable, secret_reason
from .parser import EnvFile, EnvLine

__all__ = ["Change", "SyncResult", "sync"]

APPENDED_MARKER = "# Added by envdoc sync - review and document these"


@dataclass(frozen=True)
class Change:
    action: str  # "added" | "added-blank" | "redacted" | "pruned"
    key: str
    detail: str = ""

    def describe(self) -> str:
        verbs = {
            "added": "added (value kept)",
            "added-blank": "added (value withheld)",
            "redacted": "blanked a secret",
            "pruned": "removed",
        }
        text = f"{verbs.get(self.action, self.action)}: {self.key}"
        return f"{text} - {self.detail}" if self.detail else text


@dataclass
class SyncResult:
    text: str
    changes: List[Change] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def _family(key: str) -> str:
    """``DB_HOST`` -> ``DB``; ``REDIS`` -> ``REDIS``."""
    return key.split("_", 1)[0].upper()


def _eol(*files: EnvFile) -> str:
    for f in files:
        for ln in f.lines:
            if ln.raw.endswith("\r\n"):
                return "\r\n"
            if ln.raw.endswith("\n"):
                return "\n"
    return "\n"


def _blank_line(key: str, exported: bool, eol: str) -> str:
    return f"{'export ' if exported else ''}{key}={eol}"


def _carry_line(src: EnvLine, eol: str) -> str:
    """Reuse the .env line verbatim (quoting and all), normalising EOL."""
    body = src.raw.rstrip("\r\n")
    return body + eol


def sync(
    env: EnvFile,
    example: Optional[EnvFile],
    *,
    prune: bool = False,
    redact: bool = True,
) -> SyncResult:
    """Return the updated example text and a list of what changed.

    Args:
        env: the real environment file.
        example: the current example, or ``None`` to create one.
        prune: remove example keys that ``.env`` no longer sets.
        redact: blank values in the example that look like real secrets.
    """
    example = example if example is not None else EnvFile()
    eol = _eol(example, env)
    changes: List[Change] = []

    # Work on raw strings, one per logical line, so insertion is simple.
    out: List[str] = [ln.raw for ln in example.lines]
    if out and not out[-1].endswith(("\n", "\r\n")):
        out[-1] += eol

    present = set(example.keys())
    env_keys = set(env.keys())

    # 1. Redact and prune in place, walking the existing example.
    kept_lines: List[str] = []
    kept_meta: List[Optional[EnvLine]] = []
    for raw, ln in zip(out, example.lines):
        if ln.is_entry and prune and ln.key not in env_keys:
            changes.append(Change("pruned", ln.key or "", "not set in .env"))
            continue
        if ln.is_entry and redact:
            reason = secret_reason(ln.key or "", ln.value or "")
            if reason:
                raw = _blank_line(ln.key or "", ln.exported, eol)
                changes.append(Change("redacted", ln.key or "", reason))
        kept_lines.append(raw)
        kept_meta.append(ln)

    # 2. Work out where each missing key belongs.
    last_of_family: Dict[str, int] = {}
    for idx, ln in enumerate(kept_meta):
        if ln is not None and ln.is_entry and ln.key:
            last_of_family[_family(ln.key)] = idx

    inserts: Dict[int, List[str]] = {}
    tail: List[str] = []
    for key in env.keys():
        if key in present:
            continue
        src = env.get(key)
        assert src is not None
        if publishable(key, src.value or ""):
            line = _carry_line(src, eol)
            changes.append(Change("added", key))
        else:
            line = _blank_line(key, src.exported, eol)
            changes.append(Change("added-blank", key))

        anchor = last_of_family.get(_family(key))
        if anchor is None:
            tail.append(line)
        else:
            inserts.setdefault(anchor, []).append(line)

    # 3. Assemble.
    final: List[str] = []
    for idx, raw in enumerate(kept_lines):
        final.append(raw)
        final.extend(inserts.get(idx, []))

    if tail:
        if final and final[-1].strip():
            final.append(eol)
        final.append(APPENDED_MARKER + eol)
        final.extend(tail)

    return SyncResult(text="".join(final), changes=changes)
