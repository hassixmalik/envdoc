"""Find environment variables the code actually reads.

Comparing ``.env`` with ``.env.example`` only catches drift between two
files that might both be wrong. The ground truth is the code: if
``config/services.php`` calls ``env('STRIPE_SECRET')``, the app needs it
whether or not either file mentions it.

Each reference records whether the code supplies a fallback:

* ``env('DB_HOST', '127.0.0.1')`` - optional, the app copes without it.
* ``env('STRIPE_SECRET')`` - required; unset, it is ``null`` at runtime.
* ``os.environ['X']`` - required; unset, it raises ``KeyError``.
* ``os.environ.get('X')`` / ``os.getenv('X')`` - optional.
* ``process.env.X ?? 'd'`` / ``process.env.X || 'd'`` - optional;
  bare ``process.env.X`` - required.

That split is what makes this usable on a real Laravel app: stock
``config/*.php`` reads well over a hundred variables, nearly all with
defaults, and reporting every one would bury the few that matter.

Framework-owned keys are a separate case. Stock Laravel config writes
``env('AWS_ACCESS_KEY_ID')`` with no default on purpose - ``null`` is the
idiom for "this driver is not in use" - so treating every no-default call
as required flags dozens of keys on an untouched Laravel app. Laravel 11+
ships that stock config in ``vendor/laravel/framework/config``, so
:func:`framework_keys` reads it and those keys are treated as optional,
matched to whatever version the project actually has installed.

This is pattern matching, not parsing. Dynamic names
(``env($prefix.'_HOST')``) are invisible to it, and a reference inside a
comment counts. Both are acceptable for a drift check; neither is
acceptable for a security boundary, and this is not used as one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Pattern, Sequence, Set, Tuple

from .check import ERROR, WARNING, Finding
from .parser import EnvFile
from .scan import SKIP_DIRS

__all__ = ["Reference", "code_findings", "find_references", "framework_keys", "scan_text"]

_NAME = r"([A-Z_][A-Z0-9_]*)"
_Q = r"""['"]"""

# Variables a toolchain provides itself; never expected in an env file.
_BUILTINS = {
    "MODE", "DEV", "PROD", "SSR", "BASE_URL",  # Vite import.meta.env
    "NODE_ENV", "HOME", "PATH", "PWD", "USER", "SHELL", "TMPDIR", "CI",
}

# (language, compiled pattern, required?) - for patterns where the
# default is decided by the shape of the call itself.
_FIXED: List[Tuple[str, Pattern[str], bool]] = [
    ("python", re.compile(rf"os\.environ\[\s*{_Q}{_NAME}{_Q}\s*\]"), True),
    ("python", re.compile(rf"os\.environ\.get\(\s*{_Q}{_NAME}{_Q}"), False),
    ("python", re.compile(rf"os\.getenv\(\s*{_Q}{_NAME}{_Q}"), False),
    ("php", re.compile(rf"\bgetenv\(\s*{_Q}{_NAME}{_Q}\s*\)"), True),
    ("php", re.compile(rf"\$_ENV\[\s*{_Q}{_NAME}{_Q}\s*\]"), True),
]

# Laravel env(): the presence of a second argument decides.
_PHP_ENV = re.compile(rf"(?<![\w>$:])env\(\s*{_Q}{_NAME}{_Q}\s*(,|\))")

# JS: a trailing ?? or || means a fallback is supplied.
_JS_ENV = re.compile(
    rf"(?:process\.env\.{_NAME}|process\.env\[\s*{_Q}{_NAME}{_Q}\s*\]"
    rf"|import\.meta\.env\.{_NAME})(\s*(?:\?\?|\|\|))?"
)

_EXTENSIONS: Dict[str, str] = {
    ".php": "php",
    ".py": "python",
    ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
    ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js", ".vue": "js",
    ".svelte": "js",
}


@dataclass(frozen=True)
class Reference:
    key: str
    path: Path
    line: int
    required: bool
    language: str


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(text: str, language: str, path: Path = Path("<text>")) -> List[Reference]:
    """Find references in one file's source."""
    refs: List[Reference] = []

    if language == "php":
        for m in _PHP_ENV.finditer(text):
            refs.append(
                Reference(m.group(1), path, _line_of(text, m.start()), m.group(2) == ")", "php")
            )
    if language == "js":
        for m in _JS_ENV.finditer(text):
            key = m.group(1) or m.group(2) or m.group(3)
            refs.append(
                Reference(key, path, _line_of(text, m.start()), not m.group(4), "js")
            )
    for lang, pattern, required in _FIXED:
        if lang != language:
            continue
        for m in pattern.finditer(text):
            refs.append(Reference(m.group(1), path, _line_of(text, m.start()), required, lang))

    return [r for r in refs if r.key not in _BUILTINS]


def find_references(root: str | Path, *, max_depth: int = 8) -> List[Reference]:
    """Scan a project tree for env references."""
    root = Path(root)
    refs: List[Reference] = []
    root_depth = len(root.resolve().parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        depth = len(here.resolve().parts) - root_depth
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".") and depth < max_depth
        ]
        for name in filenames:
            path = here / name
            lang = _EXTENSIONS.get(path.suffix.lower())
            if lang is None:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            refs.extend(scan_text(text, lang, path))
    return refs


def framework_keys(root: str | Path) -> Set[str]:
    """Keys read by the installed framework's own stock configuration.

    Currently Laravel: everything ``vendor/laravel/framework/config/*.php``
    reads. Returns an empty set when the framework is not installed, in
    which case those keys are judged like any other.
    """
    stock = Path(root) / "vendor" / "laravel" / "framework" / "config"
    keys: Set[str] = set()
    if not stock.is_dir():
        return keys
    for path in stock.glob("*.php"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        keys.update(r.key for r in scan_text(text, "php", path))
    return keys


def _by_key(refs: Iterable[Reference]) -> Dict[str, List[Reference]]:
    out: Dict[str, List[Reference]] = {}
    for r in refs:
        out.setdefault(r.key, []).append(r)
    return out


def code_findings(
    refs: Sequence[Reference],
    example: EnvFile,
    *,
    include_optional: bool = False,
    include_unused: bool = False,
    ignore_keys: Iterable[str] = (),
    framework: Iterable[str] = (),
) -> List[Finding]:
    """Compare what the code reads with what the example documents.

    * read with no fallback, absent from the example - **error**
    * read with a fallback, absent - warning, only with ``include_optional``
    * documented but never read - warning, only with ``include_unused``
      (off by default: frameworks read variables from ``vendor/``, which is
      not scanned, so this would flag e.g. ``PHP_CLI_SERVER_WORKERS``)
    """
    ignored: Set[str] = set(ignore_keys)
    stock: Set[str] = set(framework)
    documented = set(example.keys())
    grouped = _by_key(refs)
    out: List[Finding] = []

    for key in sorted(grouped):
        if key in documented or key in ignored:
            continue
        uses = grouped[key]
        # A framework's stock keys are optional by convention, whatever
        # the call looks like.
        required = [] if key in stock else [r for r in uses if r.required]
        first = (required or uses)[0]
        # The location is already the finding's file:line prefix; the
        # message only adds how many other places read the key.
        more = f" (+{len(uses) - 1} more)" if len(uses) > 1 else ""
        if required:
            out.append(
                Finding(
                    "undocumented-required",
                    ERROR,
                    f"{key} is read with no fallback{more} "
                    "but is not in .env.example",
                    key=key,
                    path=first.path,
                    line=first.line,
                )
            )
        elif include_optional:
            out.append(
                Finding(
                    "undocumented-optional",
                    WARNING,
                    f"{key} is read with a fallback{more} "
                    "but is not in .env.example",
                    key=key,
                    path=first.path,
                    line=first.line,
                )
            )

    if include_unused:
        for key in example.keys():
            if key in grouped or key in ignored:
                continue
            line = example.get(key)
            out.append(
                Finding(
                    "unused-key",
                    WARNING,
                    f"{key} is documented but no scanned code reads it",
                    key=key,
                    path=example.path,
                    line=line.lineno if line else None,
                )
            )
    return out
