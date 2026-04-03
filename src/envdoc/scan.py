"""Find env files and dumps that are one ``git add .`` away from leaking.

What it looks for, inside a directory tree:

* env-style files (``.env``, ``.env.production``, ``server.info.env``,
  ``credentials.env``...) that git **tracks** - already leaked - or that
  are **not ignored** - one careless ``git add .`` from leaking;
* database dumps (``*.sql``, ``*.sql.gz``, ``*.dump``) in the same state;
* repositories whose ``.gitignore`` does not cover ``.env`` at all, even
  if no ``.env`` exists yet;
* for every env file found, which values look like real secrets.

Ignore rules are git's business, so this asks git (``check-ignore`` and
``ls-files``) instead of re-implementing ``.gitignore`` matching, which is
subtle enough - negations, directory rules, nested files - to get wrong.
Files outside any repository cannot be committed by accident, but are
reported when they hold secrets: a credential file sitting loose in a
projects folder is how the next repository gets created around it.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .check import ERROR, WARNING, Finding
from .classify import secret_reason
from .parser import ParseError, parse_file

__all__ = ["ScanResult", "is_dump_file", "is_env_file", "scan"]

SKIP_DIRS = {
    ".git", "node_modules", "vendor", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
    ".next", ".nuxt", ".idea", ".vscode", "storage",
}

# Suffixes that mark a file as documentation rather than configuration.
_TEMPLATE_RE = re.compile(r"\.(?:example|sample|template|dist|tpl|defaults?)$", re.I)
_ENV_NAME_RE = re.compile(r"^(?:\.env(?:\..+)?|.+\.env)$", re.I)
_DUMP_RE = re.compile(r"\.(?:sql|sql\.gz|sql\.zip|sql\.bz2|dump|bak)$", re.I)


def is_env_file(path: Path) -> bool:
    name = path.name
    return bool(_ENV_NAME_RE.match(name)) and not _TEMPLATE_RE.search(name)


def is_dump_file(path: Path) -> bool:
    return bool(_DUMP_RE.search(path.name))


@dataclass
class ScanResult:
    root: Path
    findings: List[Finding] = field(default_factory=list)
    files_examined: int = 0
    repos: List[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == ERROR for f in self.findings)


def _git(repo: Path, *args: str, stdin: Optional[bytes] = None) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            input=stdin,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _repo_root(path: Path) -> Optional[Path]:
    proc = _git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel")
    if proc is None or proc.returncode != 0:
        return None
    return Path(proc.stdout.decode().strip()).resolve()


def _tracked(repo: Path) -> Set[Path]:
    proc = _git(repo, "ls-files", "-z")
    if proc is None or proc.returncode != 0:
        return set()
    return {
        (repo / p).resolve()
        for p in proc.stdout.decode(errors="replace").split("\0")
        if p
    }


def _ignored(repo: Path, paths: Iterable[Path]) -> Set[Path]:
    rel = [os.path.relpath(p, repo) for p in paths]
    if not rel:
        return set()
    payload = "\0".join(rel).encode() + b"\0"
    proc = _git(repo, "check-ignore", "-z", "--stdin", stdin=payload)
    # Exit 1 means "none of these are ignored", which is a valid answer.
    if proc is None or proc.returncode not in (0, 1):
        return set()
    return {
        (repo / p).resolve()
        for p in proc.stdout.decode(errors="replace").split("\0")
        if p
    }


def _walk(root: Path, max_depth: int) -> Iterable[Path]:
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        depth = len(here.parts) - root_depth
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and depth < max_depth
        ]
        for name in filenames:
            yield here / name


def _secret_findings(path: Path, severity: str) -> List[Finding]:
    try:
        env = parse_file(path)
    except ParseError:
        return []
    out = []
    for ln in env.entries():
        reason = secret_reason(ln.key or "", ln.value or "")
        if reason:
            out.append(
                Finding(
                    "secret-value",
                    severity,
                    f"{ln.key} holds what looks like a real secret ({reason})",
                    key=ln.key,
                    path=path,
                    line=ln.lineno,
                )
            )
    return out


def scan(root: str | Path, *, max_depth: int = 6) -> ScanResult:
    """Scan ``root`` and every repository beneath it."""
    root = Path(root).resolve()
    result = ScanResult(root=root)

    candidates: List[Path] = []
    for path in _walk(root, max_depth):
        if is_env_file(path) or is_dump_file(path):
            candidates.append(path.resolve())
    result.files_examined = len(candidates)

    # Group candidates by the repository that owns them (None = no repo).
    by_repo: Dict[Optional[Path], List[Path]] = {}
    for path in candidates:
        by_repo.setdefault(_repo_root(path), []).append(path)

    # Repositories under root with no candidate files still deserve the
    # .gitignore check, so find them too.
    repos: Set[Path] = {r for r in by_repo if r is not None}
    for dirpath, dirnames, _ in os.walk(root):
        if ".git" in dirnames:
            repos.add(Path(dirpath).resolve())
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    own = _repo_root(root)
    if own is not None:
        repos.add(own)
    result.repos = sorted(repos)

    for repo in result.repos:
        if not _ignored(repo, [repo / ".env"]):
            result.findings.append(
                # A warning: nothing has leaked yet. If an unignored .env
                # does exist, unignored-env-file reports it as an error.
                Finding(
                    "gitignore-missing-env",
                    WARNING,
                    ".gitignore does not cover .env - the first one created "
                    "will be committed by 'git add .'",
                    path=repo / ".gitignore",
                )
            )

    for repo, paths in sorted(by_repo.items(), key=lambda kv: str(kv[0])):
        if repo is None:
            for path in paths:
                if is_env_file(path):
                    result.findings.extend(_secret_findings(path, WARNING))
            continue

        tracked = _tracked(repo)
        ignored = _ignored(repo, paths)
        for path in sorted(paths):
            kind = "env file" if is_env_file(path) else "database dump"
            if path in tracked:
                result.findings.append(
                    Finding(
                        "tracked-env-file" if is_env_file(path) else "tracked-dump",
                        ERROR,
                        f"{kind} is committed to git - rotate anything in it, "
                        "then 'git rm --cached' and ignore it",
                        path=path,
                    )
                )
            elif path not in ignored:
                result.findings.append(
                    Finding(
                        "unignored-env-file" if is_env_file(path) else "unignored-dump",
                        ERROR if is_env_file(path) else WARNING,
                        f"{kind} is not ignored - 'git add .' would commit it",
                        path=path,
                    )
                )
            if is_env_file(path) and (path in tracked or path not in ignored):
                result.findings.extend(_secret_findings(path, ERROR))

    return result
