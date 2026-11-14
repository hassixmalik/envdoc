"""Command line interface.

    envdoc check   compare .env, .env.example and the code
    envdoc sync    bring .env.example up to date, without copying secrets
    envdoc scan    find env files and dumps git would commit
    envdoc init    set up .gitignore, .env.example and the hook in one go
    envdoc hook    install, remove or run the pre-commit hook
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set

from . import __version__
from . import hook as hookmod
from ._io import write_exact
from .check import Finding, Report, compare, leaked_secrets
from .init import init as init_repo
from .parser import EnvFile, ParseError, parse_file
from .refs import code_findings, find_references, framework_keys
from .scan import scan
from .sync import sync

IGNORE_FILE = ".envdocignore"


# -- helpers -------------------------------------------------------------


def _read_ignore_file(root: Path) -> Set[str]:
    """Keys listed in .envdocignore, one per line, # for comments."""
    path = root / IGNORE_FILE
    if not path.is_file():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            keys.add(line)
    return keys


def _load(path: Path) -> Optional[EnvFile]:
    if not path.is_file():
        return None
    return parse_file(path)


def _emit(findings: Sequence[Finding], fmt: str, out=None) -> None:
    out = out or sys.stdout
    if fmt == "json":
        payload = [
            {
                "code": f.code,
                "severity": f.severity,
                "message": f.message,
                "key": f.key,
                "path": str(f.path) if f.path else None,
                "line": f.line,
            }
            for f in findings
        ]
        print(json.dumps(payload, indent=2), file=out)
        return
    if fmt == "github":
        # https://docs.github.com/actions/reference/workflow-commands
        for f in findings:
            level = "error" if f.severity == "error" else "warning"
            loc = ""
            if f.path:
                loc = f" file={f.path}"
                if f.line:
                    loc += f",line={f.line}"
            msg = f"[{f.code}] {f.message}".replace("%", "%25").replace("\n", "%0A")
            print(f"::{level}{loc}::{msg}", file=out)
        return
    for f in findings:
        print(f.format(), file=out)
    # The summary goes to stderr; flush so captured logs (CI) keep order.
    out.flush()


def _summary(report: Report) -> str:
    e, w = len(report.errors), len(report.warnings)
    if not e and not w:
        return "envdoc: all clear"
    parts = []
    if e:
        parts.append(f"{e} error{'s' * (e != 1)}")
    if w:
        parts.append(f"{w} warning{'s' * (w != 1)}")
    return "envdoc: " + ", ".join(parts)


# -- commands ------------------------------------------------------------


def _no_example(args: argparse.Namespace, root: Path, env, ignore: Set[str]) -> int:
    """Decide what a missing .env.example means.

    * a .env exists - the example should too: error, and sync can build it;
    * no .env, but code reads variables with no fallback - error, those
      need documenting somewhere;
    * no .env and nothing required - the project does not use env
      configuration, so there is nothing to check.
    """
    if env is not None:
        print(
            f"envdoc: {args.env} exists but {args.example} does not - "
            "create it with 'envdoc sync'",
            file=sys.stderr,
        )
        return 1

    needed: List[str] = []
    if args.code:
        stock = framework_keys(root)
        needed = sorted(
            {
                r.key
                for r in find_references(root)
                if r.required and r.key not in stock and r.key not in ignore
            }
        )
    if needed:
        shown = ", ".join(needed[:5]) + (f" (+{len(needed) - 5} more)" if len(needed) > 5 else "")
        print(
            f"envdoc: no {args.example}, but the code reads {len(needed)} variable(s) "
            f"with no fallback: {shown}",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(
            f"envdoc: no {args.env}, {args.example} or required env reads - nothing to check",
            file=sys.stderr,
        )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root)
    env_path = root / args.env
    example_path = root / args.example
    ignore = _read_ignore_file(root) | set(args.ignore or [])

    try:
        env = _load(env_path)
        example = _load(example_path)
    except ParseError as exc:
        print(f"envdoc: {exc}", file=sys.stderr)
        return 2

    if example is None:
        return _no_example(args, root, env, ignore)

    report = Report()
    if env is not None:
        report.extend(compare(env, example, ignore_keys=ignore).findings)
    elif not args.quiet and args.format == "text":
        # Normal in CI: there is no .env on a build runner.
        print(f"envdoc: no {args.env} - checking {args.example} and code only", file=sys.stderr)

    report.extend(leaked_secrets(example))

    if args.code:
        refs = find_references(root)
        # A key already reported as missing-in-example has the same fix -
        # add it to the example - so do not report it a second time.
        already = {f.key for f in report.findings if f.code == "missing-in-example"}
        report.extend(
            f
            for f in code_findings(
                refs,
                example,
                include_optional=args.optional,
                include_unused=args.unused,
                ignore_keys=ignore,
                framework=framework_keys(root),
            )
            if f.key not in already
        )

    report = report.without(args.skip or [])
    if args.strict:
        report = report.promote_warnings()

    _emit(report.findings, args.format)
    if args.format == "text" and not args.quiet:
        print(_summary(report), file=sys.stderr)
    return 0 if report.ok else 1


def cmd_sync(args: argparse.Namespace) -> int:
    root = Path(args.root)
    env_path = root / args.env
    example_path = root / args.example
    try:
        env = _load(env_path)
        example = _load(example_path)
    except ParseError as exc:
        print(f"envdoc: {exc}", file=sys.stderr)
        return 2
    if env is None:
        print(f"envdoc: no {args.env} in {root} - nothing to sync from", file=sys.stderr)
        return 1

    result = sync(env, example, prune=args.prune, redact=not args.no_redact)
    if not result.changed:
        print(f"envdoc: {args.example} is already up to date")
        return 0

    for change in result.changes:
        print(f"  {change.describe()}")

    if args.dry_run:
        before = example.render() if example else ""
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            result.text.splitlines(keepends=True),
            fromfile=f"a/{args.example}",
            tofile=f"b/{args.example}",
        )
        sys.stdout.writelines(diff)
        print(f"\nenvdoc: dry run - {args.example} not written")
        return 0

    write_exact(example_path, result.text)
    print(f"envdoc: updated {example_path} ({len(result.changes)} change(s))")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    result = scan(args.path, max_depth=args.depth)
    findings = [f for f in result.findings if f.code not in set(args.skip or [])]
    _emit(findings, args.format)
    if args.format == "text":
        report = Report(findings)
        print(
            f"{_summary(report)} ({len(result.repos)} repo(s), "
            f"{result.files_examined} candidate file(s))",
            file=sys.stderr,
        )
    return 0 if Report(findings).ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    try:
        result = init_repo(args.root, hook=args.hook)
    except ParseError as exc:
        print(f"envdoc: {exc}", file=sys.stderr)
        return 2
    for line in result.done:
        print(f"  done:    {line}")
    for line in result.skipped:
        print(f"  skipped: {line}")
    for line in result.problems:
        print(f"  problem: {line}", file=sys.stderr)
    return 1 if result.problems else 0


def cmd_hook(args: argparse.Namespace) -> int:
    try:
        if args.action == "install":
            path = hookmod.install(args.root, force=args.force)
            print(f"envdoc: installed {path}")
            return 0
        if args.action == "uninstall":
            path = hookmod.uninstall(args.root)
            print(f"envdoc: removed {path}" if path else "envdoc: no hook installed")
            return 0
    except hookmod.HookError as exc:
        print(f"envdoc: {exc}", file=sys.stderr)
        return 1
    return hookmod.run(args.root, strict=args.strict)


# -- parser --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envdoc",
        description="Keep .env and .env.example honest, and catch secrets before they ship.",
    )
    parser.add_argument("--version", action="version", version=f"envdoc {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def files(p):
        p.add_argument("-C", "--root", default=".", help="project directory (default: .)")
        p.add_argument("--env", default=".env", help="env file name (default: .env)")
        p.add_argument("--example", default=".env.example",
                       help="example file name (default: .env.example)")

    def fmt(p):
        p.add_argument("--format", choices=["text", "json", "github"], default="text",
                       help="output format; 'github' emits Actions annotations")
        p.add_argument("--skip", action="append", metavar="CODE",
                       help="suppress a finding code; repeatable")

    pc = sub.add_parser("check", help="compare .env, .env.example and the code")
    files(pc)
    fmt(pc)
    pc.add_argument("--no-code", dest="code", action="store_false",
                    help="do not scan source code for env references")
    pc.add_argument("--optional", action="store_true",
                    help="also report undocumented keys the code reads with a fallback")
    pc.add_argument("--unused", action="store_true",
                    help="also report documented keys no scanned code reads")
    pc.add_argument("--strict", action="store_true", help="treat warnings as errors")
    pc.add_argument("--ignore", action="append", metavar="KEY",
                    help=f"ignore a key; repeatable (or list keys in {IGNORE_FILE})")
    pc.add_argument("-q", "--quiet", action="store_true", help="no summary line")
    pc.set_defaults(func=cmd_check)

    ps = sub.add_parser("sync", help="update .env.example from .env, without secrets")
    files(ps)
    ps.add_argument("-n", "--dry-run", action="store_true", help="show a diff, write nothing")
    ps.add_argument("--prune", action="store_true",
                    help="remove example keys that .env no longer sets")
    ps.add_argument("--no-redact", action="store_true",
                    help="leave real-looking secrets already in the example alone")
    ps.set_defaults(func=cmd_sync)

    pn = sub.add_parser("scan", help="find env files and dumps git would commit")
    pn.add_argument("path", nargs="?", default=".", help="directory to scan (default: .)")
    pn.add_argument("--depth", type=int, default=6, help="max directory depth (default: 6)")
    fmt(pn)
    pn.set_defaults(func=cmd_scan)

    pi = sub.add_parser("init", help="set up .gitignore, .env.example and the hook")
    pi.add_argument("-C", "--root", default=".", help="repository (default: .)")
    pi.add_argument("--no-hook", dest="hook", action="store_false",
                    help="skip installing the pre-commit hook")
    pi.set_defaults(func=cmd_init)

    ph = sub.add_parser("hook", help="install, remove or run the pre-commit hook")
    ph.add_argument("action", choices=["install", "uninstall", "run"])
    ph.add_argument("-C", "--root", default=".", help="repository (default: .)")
    ph.add_argument("--force", action="store_true",
                    help="install: replace a pre-commit hook envdoc did not write")
    ph.add_argument("--strict", action="store_true",
                    help="run: block on .env/.env.example drift too")
    ph.set_defaults(func=cmd_hook)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
