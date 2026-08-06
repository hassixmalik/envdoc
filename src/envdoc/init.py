"""One-shot setup for a repository.

``envdoc init`` does the three things ``scan`` most often complains about:

1. makes sure ``.gitignore`` covers ``.env`` (and ``.env.*``, while keeping
   ``.env.example`` committable);
2. creates ``.env.example`` from ``.env`` if there is no example yet,
   through the same secret-withholding path as ``sync``;
3. installs the pre-commit hook.

Each step is idempotent and reports what it did, so running ``init`` on an
already-configured repository changes nothing and says so.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from . import hook as hookmod
from .parser import parse_file
from .sync import sync

__all__ = ["GITIGNORE_BLOCK", "InitResult", "init"]

GITIGNORE_BLOCK = """
# Environment files (added by envdoc init)
.env
.env.*
!.env.example
!.env.*.example
"""


@dataclass
class InitResult:
    done: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def _env_ignored(repo: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", ".env"],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _example_ignored(repo: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", ".env.example"],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def init(root: str | Path = ".", *, hook: bool = True) -> InitResult:
    root = Path(root).resolve()
    result = InitResult()

    # 1. .gitignore
    gitignore = root / ".gitignore"
    if _env_ignored(root):
        result.skipped.append(".gitignore already covers .env")
    else:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        sep = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(existing + sep + GITIGNORE_BLOCK.lstrip("\n"), encoding="utf-8")
        result.done.append("added .env rules to .gitignore")

    # A broad '.env*' rule elsewhere can swallow the example too; say so
    # rather than silently editing someone's ignore rules a second time.
    if _example_ignored(root):
        result.problems.append(
            ".env.example is ignored by .gitignore - add '!.env.example' "
            "after the rule that matches it, or it can never be committed"
        )

    # 2. .env.example
    env_path = root / ".env"
    example_path = root / ".env.example"
    if example_path.exists():
        result.skipped.append(".env.example already exists (use 'envdoc sync' to update it)")
    elif env_path.exists():
        res = sync(parse_file(env_path), None)
        example_path.write_text(res.text, encoding="utf-8", newline="")
        withheld = sum(1 for c in res.changes if c.action == "added-blank")
        result.done.append(
            f"created .env.example with {len(res.changes)} key(s), "
            f"{withheld} value(s) withheld"
        )
    else:
        result.skipped.append("no .env to build an example from")

    # 3. hook
    if hook:
        try:
            if hookmod.is_installed(root):
                result.skipped.append("pre-commit hook already installed")
            else:
                path = hookmod.install(root)
                result.done.append(f"installed pre-commit hook at {path}")
        except hookmod.HookError as exc:
            result.problems.append(str(exc))

    return result
