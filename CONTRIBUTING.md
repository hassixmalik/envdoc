# Contributing

Issues and pull requests are welcome.

## Setup

```bash
git clone https://github.com/hassixmalik/envdoc.git
cd envdoc
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

CI runs the suite on Python 3.9 to 3.13 across Linux, macOS and Windows.

## The one rule for tests

**Never write a real-looking secret into a source file**, tests included.
Build fake tokens from fragments at runtime:

```python
FAKE_AWS = "AKIA" + "IOSFODNN7" + "EXAMPLE"
```

A literal token in a test file gets flagged by GitHub push protection, by
every secret scanner a downstream user runs, and by this tool's own CI
dogfood job.

## Adding a token pattern

Patterns live in `_TOKEN_PATTERNS` in `src/envdoc/classify.py`.

1. Prefer tokens that announce themselves: a fixed prefix (`ghp_`,
   `sk_live_`, `xoxb-`) or a fixed structure (PEM headers, JWTs). Those
   have essentially no false positives.
2. Anchor with `\b` so the pattern does not fire inside longer strings.
3. Add a case to `TestSecretReason.test_known_token_shapes`, with the
   token built from fragments.
4. Make sure the ordinary-values test still passes. URLs, paths and prose
   are where false positives come from.

## Adding a language to the code scanner

References are matched in `src/envdoc/refs.py`. For each new pattern,
decide whether it is **required** (the code breaks when the variable is
unset) or **optional** (a fallback is supplied). That distinction is what
keeps the report short, so it matters more than catching every syntax.
Add the file extension to `_EXTENSIONS` and tests to `tests/test_refs.py`.

## Design decisions worth keeping

- **No runtime dependencies.** envdoc runs inside pre-commit hooks and CI,
  where every install is latency and another way to fail.
- **Never print a value.** Findings name keys and reasons. A test asserts
  this for `check`. Keep it that way for anything new.
- **Ask git, do not reimplement it.** Ignore rules come from
  `git check-ignore`, and staged content from `git show :path`.
- **The hook must not become the reason nobody can commit.** If the
  checker itself breaks, it lets the commit through. Drift warns rather
  than blocks.
