# Changelog

## 0.1.1

- Published to PyPI as `envdoc-cli` (`pip install envdoc-cli`). The command is still `envdoc`.
- Release workflow: publishing a GitHub release builds and uploads to PyPI with trusted publishing.
- README: demo, project page link and install from PyPI.
- Fix: `sync` and `init` crashed on Python 3.9 (`write_text(newline=)` is 3.10+).
- Fix: findings printed backslash paths on Windows; locations now always use `/`.

## 0.1.0

First release.

### Commands

- `envdoc check` compares `.env`, `.env.example` and the variables the code
  actually reads. Exits 1 on errors. Output as text, JSON, or GitHub
  Actions annotations.
- `envdoc sync` adds missing keys to `.env.example` without copying
  secrets. New keys go next to their family, and comments, order, quoting
  and line endings are preserved.
- `envdoc scan` finds env files and database dumps that git tracks or does
  not ignore, across every repository under a directory.
- `envdoc hook` installs a pre-commit hook that blocks env files, dumps and
  leaked secrets. It reads staged content rather than the working tree.
- `envdoc init` sets up `.gitignore`, `.env.example` and the hook in one
  idempotent step.

### Details

- A layout-preserving parser covering the common subset of phpdotenv,
  python-dotenv and Node dotenv, including multi-line quoted values.
- Detection of known token formats (AWS, GitHub, GitLab, Slack, Stripe,
  Google, OpenAI and Anthropic-style keys, SendGrid, Twilio, JWTs, PEM
  private keys, Laravel app keys, credentials in URLs), with entropy as a
  fallback for token-shaped values.
- Code scanning for PHP/Laravel, JS/TS/Vue/Vite and Python, distinguishing
  required reads from reads with a fallback.
- Laravel's stock configuration is read from the installed
  `vendor/laravel/framework/config` at runtime, so its intentionally
  default-less `env()` calls are not reported as errors.
- No runtime dependencies. Python 3.9+.
