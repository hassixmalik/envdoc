"""Pre-commit hook: stop secrets at the door.

``envdoc hook run`` looks only at what is being committed, and reads the
*staged* content via ``git show :path`` rather than the file on disk -
the two differ whenever someone stages a file and keeps editing it, and
the index is what actually gets committed.

It blocks a commit that would add:

* an env file (``.env``, ``.env.production``...) or a database dump;
* a ``.env.example`` (or other template) containing a real-looking secret.

Drift between ``.env`` and the example is reported but does not block -
a hook that refuses commits over documentation is a hook people delete.
``--strict`` makes it block too.

Installation respects ``core.hooksPath`` and will not overwrite a hook it
did not write.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .check import ERROR, Finding, Report, compare, leaked_secrets
from .parser import parse
from .scan import is_dump_file, is_env_file

__all__ = ["MARKER", "HookError", "check_staged", "hook_path", "install", "is_installed", "uninstall"]

MARKER = "# envdoc pre-commit hook"

_SCRIPT = f"""#!/bin/sh
{MARKER} - remove with 'envdoc hook uninstall'
# Bypass once with: git commit --no-verify
if command -v envdoc >/dev/null 2>&1; then
    exec envdoc hook run
fi
exec "{{python}}" -m envdoc hook run
"""

_TEMPLATE_NAMES = (".example", ".sample", ".template", ".dist")


class HookError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )


def _hooks_dir(repo: Path) -> Path:
    proc = _git(repo, "rev-parse", "--git-path", "hooks")
    if proc.returncode != 0:
        raise HookError(f"{repo} is not inside a git repository")
    path = Path(proc.stdout.decode().strip())
    return path if path.is_absolute() else (repo / path)


def _repo_root(start: Path) -> Path:
    proc = _git(start, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        raise HookError(f"{start} is not inside a git repository")
    return Path(proc.stdout.decode().strip())


def hook_path(start: str | Path = ".") -> Path:
    """Where the pre-commit hook lives, honouring core.hooksPath."""
    return _hooks_dir(_repo_root(Path(start))) / "pre-commit"


def is_installed(start: str | Path = ".") -> bool:
    target = hook_path(start)
    return target.exists() and MARKER in target.read_text(encoding="utf-8", errors="replace")


def install(start: str | Path = ".", *, force: bool = False) -> Path:
    """Write the pre-commit hook. Returns its path."""
    repo = _repo_root(Path(start))
    hooks = _hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "pre-commit"

    if target.exists():
        current = target.read_text(encoding="utf-8", errors="replace")
        if MARKER not in current and not force:
            raise HookError(
                f"{target} already exists and was not written by envdoc. "
                "Add 'envdoc hook run' to it yourself, or use --force to replace it."
            )

    target.write_text(_SCRIPT.format(python=sys.executable), encoding="utf-8")
    mode = target.stat().st_mode
    target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def uninstall(start: str | Path = ".") -> Optional[Path]:
    """Remove the hook if envdoc wrote it. Returns the path removed, if any."""
    repo = _repo_root(Path(start))
    target = _hooks_dir(repo) / "pre-commit"
    if not target.exists():
        return None
    if MARKER not in target.read_text(encoding="utf-8", errors="replace"):
        raise HookError(f"{target} was not written by envdoc; leaving it alone")
    target.unlink()
    return target


def _staged(repo: Path) -> List[str]:
    proc = _git(repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    if proc.returncode != 0:
        raise HookError(proc.stderr.decode(errors="replace").strip())
    return [p for p in proc.stdout.decode(errors="replace").split("\0") if p]


def _staged_text(repo: Path, rel: str) -> Optional[str]:
    proc = _git(repo, "show", f":{rel}")
    if proc.returncode != 0:
        return None
    try:
        return proc.stdout.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _is_template(name: str) -> bool:
    return name.startswith(".env") and name.endswith(_TEMPLATE_NAMES)


def check_staged(start: str | Path = ".", *, strict: bool = False) -> Tuple[Report, int]:
    """Inspect the index. Returns the report and how many files were staged."""
    repo = _repo_root(Path(start))
    staged = _staged(repo)
    report = Report()

    for rel in staged:
        path = Path(rel)
        if is_env_file(path):
            # Advice depends on how it got here: forced past an ignore rule,
            # or never ignored in the first place.
            forced = _git(repo, "check-ignore", "-q", "--no-index", rel).returncode == 0
            fix = (
                "it is ignored but was force-added"
                if forced
                else "add it to .gitignore"
            )
            report.add(
                Finding(
                    "staged-env-file",
                    ERROR,
                    f"env file is staged for commit ({fix}) - unstage it with "
                    f"'git restore --staged {rel}'",
                    path=path,
                )
            )
        elif is_dump_file(path):
            report.add(
                Finding(
                    "staged-dump",
                    ERROR,
                    f"database dump is staged for commit - 'git restore --staged {rel}'",
                    path=path,
                )
            )
        elif _is_template(path.name):
            text = _staged_text(repo, rel)
            if text is None:
                continue
            example = parse(text)
            example.path = path
            report.extend(leaked_secrets(example))

            # Drift: compare against the working-tree .env beside it.
            env_path = repo / path.parent / ".env"
            if env_path.is_file():
                env = parse(env_path.read_text(encoding="utf-8-sig", errors="replace"))
                env.path = path.parent / ".env"
                drift = compare(env, example).without(["missing-in-env"])
                if not strict:
                    drift = Report(
                        [
                            Finding(f.code, "warning", f.message, f.key, f.path, f.line)
                            for f in drift.findings
                        ]
                    )
                report.extend(drift.findings)

    return report, len(staged)


def run(start: str | Path = ".", *, strict: bool = False) -> int:
    """Entry point for the installed hook. Returns the exit status."""
    try:
        report, count = check_staged(start, strict=strict)
    except HookError as exc:
        print(f"envdoc: {exc}", file=sys.stderr)
        return 0  # never block a commit because the checker itself broke
    for f in report.findings:
        print(f.format(), file=sys.stderr)
    if not report.ok:
        print(
            f"\nenvdoc: commit blocked ({len(report.errors)} problem(s) in {count} "
            "staged file(s)). Bypass once with 'git commit --no-verify'.",
            file=sys.stderr,
        )
        return 1
    if report.findings:
        print("envdoc: warnings only - commit allowed", file=sys.stderr)
    return 0
