"""Compare a real env file against its example.

The failure this exists for: someone adds ``LEGACY_DB_HOST`` to their own
``.env``, the code starts depending on it, and ``.env.example`` never hears
about it. The next person to clone the repo gets an app that fails in some
confusing way with no hint of what is missing. The NMR v2 app this tool was
first run against had eleven such variables, every database credential
among them.

Each problem is a :class:`Finding` with a stable ``code`` so callers can
filter or suppress by code rather than by matching message text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .classify import secret_reason
from .parser import EnvFile

__all__ = ["ERROR", "WARNING", "Finding", "Report", "compare", "leaked_secrets"]

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    key: Optional[str] = None
    path: Optional[Path] = None
    line: Optional[int] = None

    def location(self) -> str:
        if self.path is None:
            return ""
        return f"{self.path}:{self.line}" if self.line else str(self.path)

    def format(self) -> str:
        loc = self.location()
        prefix = f"{loc}: " if loc else ""
        return f"{prefix}{self.severity} [{self.code}] {self.message}"


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: Iterable[Finding]) -> None:
        self.findings.extend(findings)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> List[str]:
        return [f.code for f in self.findings]

    def without(self, codes: Iterable[str]) -> Report:
        """A copy with the given codes suppressed."""
        drop = set(codes)
        return Report([f for f in self.findings if f.code not in drop])

    def promote_warnings(self) -> Report:
        """A copy where every warning counts as an error (``--strict``)."""
        return Report(
            [
                Finding(f.code, ERROR, f.message, f.key, f.path, f.line)
                if f.severity == WARNING
                else f
                for f in self.findings
            ]
        )


def _structural(env: EnvFile, label: str) -> List[Finding]:
    """Problems inside one file, independent of the other."""
    out: List[Finding] = []
    for key, lines in env.duplicates().items():
        out.append(
            Finding(
                "duplicate-key",
                WARNING,
                f"{key} is assigned {len(lines)} times in {label} "
                f"(lines {', '.join(map(str, lines))}); the last one wins",
                key=key,
                path=env.path,
                line=lines[-1],
            )
        )
    for ln in env.invalid():
        out.append(
            Finding(
                "invalid-line",
                ERROR,
                f"unparseable line in {label}: {ln.problem}",
                key=ln.key,
                path=env.path,
                line=ln.lineno,
            )
        )
    return out


def compare(
    env: EnvFile,
    example: EnvFile,
    *,
    ignore_keys: Iterable[str] = (),
) -> Report:
    """Check ``env`` against ``example``.

    Severity follows who gets hurt:

    * A key in ``.env`` but missing from the example is an **error** - it
      breaks the next person to clone the repo, and they cannot tell.
    * A key in the example but missing from ``.env`` is a **warning** - it
      only affects this checkout, and the developer can see the example.
    """
    report = Report()
    ignored = set(ignore_keys)

    report.extend(_structural(env, ".env"))
    report.extend(_structural(example, ".env.example"))

    env_keys = [k for k in env.keys() if k not in ignored]
    example_keys = set(example.keys())
    for key in env_keys:
        if key not in example_keys:
            line = env.get(key)
            report.add(
                Finding(
                    "missing-in-example",
                    ERROR,
                    f"{key} is set in .env but not documented in .env.example",
                    key=key,
                    path=env.path,
                    line=line.lineno if line else None,
                )
            )

    present = set(env.keys())
    for key in example.keys():
        if key in ignored or key in present:
            continue
        line = example.get(key)
        report.add(
            Finding(
                "missing-in-env",
                WARNING,
                f"{key} is documented in .env.example but not set in .env",
                key=key,
                path=example.path,
                line=line.lineno if line else None,
            )
        )

    return report


def leaked_secrets(example: EnvFile) -> List[Finding]:
    """Real-looking credentials in a file meant to be committed.

    The reverse drift: someone runs ``cp .env .env.example`` to "update the
    example", commits it, and every secret goes with it. Placeholders and
    blanks are fine; anything that classifies as a secret is an error.
    """
    out: List[Finding] = []
    for ln in example.entries():
        reason = secret_reason(ln.key or "", ln.value or "")
        if reason:
            out.append(
                Finding(
                    "secret-in-example",
                    ERROR,
                    f"{ln.key} in .env.example looks like a real secret ({reason})",
                    key=ln.key,
                    path=example.path,
                    line=ln.lineno,
                )
            )
    return out
