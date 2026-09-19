# envdoc

Keep `.env` and `.env.example` honest, and catch secrets before they ship.

```console
$ envdoc check
.env:8: error [missing-in-example] DB_SSLMODE is set in .env but not documented in .env.example
.env:10: error [missing-in-example] STRIPE_SECRET is set in .env but not documented in .env.example
config/billing.php:4: error [undocumented-required] STRIPE_WEBHOOK_SECRET is read with no fallback but is not in .env.example
envdoc: 3 errors
```

Zero dependencies. Python 3.9+. Works with Laravel, Node/Vite and Python
projects.

## The problems it fixes

**The example drifts.** Someone adds `LEGACY_DB_HOST` to their own `.env`,
the code starts depending on it, and `.env.example` never hears about it.
The next person to clone the repository gets an app that fails in a
confusing way and no hint of what is missing.

**The code needs things neither file mentions.** `config/services.php`
reads `env('STRIPE_SECRET')`, but nobody ever wrote it down anywhere.

**Secrets leak the other way.** Someone "updates the example" with
`cp .env .env.example` and commits every credential. Or a `.env.production`
is never ignored, and `git add .` picks it up.

## Install

```bash
pip install git+https://github.com/hassixmalik/envdoc.git
```

Or from a clone:

```bash
git clone https://github.com/hassixmalik/envdoc.git
cd envdoc && pip install .
```

Not on PyPI yet.

## Quick start

```console
$ envdoc init
  done:    added .env rules to .gitignore
  done:    installed pre-commit hook at .git/hooks/pre-commit
  skipped: .env.example already exists (use 'envdoc sync' to update it)
```

`init` is safe to rerun. On a configured repository it changes nothing and
says so.

## Commands

### `envdoc check`

Compares three sources of truth: your `.env`, your `.env.example`, and
what the code actually reads. Exits `1` on any error.

| Code | Severity | Meaning |
|---|---|---|
| `missing-in-example` | error | set in `.env`, not documented |
| `undocumented-required` | error | code reads it with no fallback, not documented |
| `secret-in-example` | error | `.env.example` holds a real-looking secret |
| `invalid-line` | error | a line that is not `KEY=value` |
| `missing-in-env` | warning | documented, but not set in your `.env` |
| `duplicate-key` | warning | assigned more than once; the last one wins |
| `undocumented-optional` | warning | read with a fallback, not documented (`--optional`) |
| `unused-key` | warning | documented, but no scanned code reads it (`--unused`) |

The severities follow who gets hurt. A key missing from the example breaks
the *next* person, who cannot see why, so it is an error. A key missing from
your `.env` only affects you, and you have the example open, so it is a
warning.

It runs without a `.env`, which is the normal state of a CI runner. It
still checks the code against the example and the example for leaks:

```console
$ envdoc check
envdoc: no .env - checking .env.example and code only
```

Useful flags:

```bash
envdoc check --format github     # inline annotations on the PR diff
envdoc check --format json       # for anything else
envdoc check --strict            # warnings fail too
envdoc check --skip missing-in-env
envdoc check --ignore LOCAL_ONLY_FLAG
```

Keys you never want reported can go in `.envdocignore`, one per line.

### `envdoc sync`

Writes the keys your `.env` has and the example lacks, **without copying
secrets**:

```console
$ envdoc sync
  added (value kept): DB_SSLMODE
  added (value withheld): STRIPE_SECRET
envdoc: updated .env.example (2 change(s))
```

A value is carried over only when it is generic: `local`, `true`, `3306`,
`127.0.0.1`, `require`, `${APP_NAME}`. Database names, hostnames, emails
and anything secret become `KEY=`.

New keys go where a person would put them. A new `DB_SSLMODE` lands after
the last `DB_*` key, not at the bottom of the file. Keys with no family yet
are appended under a marker comment so the diff is easy to review.

Everything else in the file is preserved: comments, blank lines, order,
quoting, `export` prefixes, CRLF line endings.

```bash
envdoc sync --dry-run    # print a diff, write nothing
envdoc sync --prune      # also remove keys .env no longer sets
```

`sync` also blanks real secrets already sitting in the example. Pass
`--no-redact` to leave them alone.

### `envdoc scan`

Finds env files and database dumps that are one `git add .` away from
leaking, across every repository under a directory:

```console
$ envdoc scan
.gitignore: warning [gitignore-missing-env] .gitignore does not cover .env - the first one created will be committed by 'git add .'
.env: error [unignored-env-file] env file is not ignored - 'git add .' would commit it
.env:7: error [secret-value] DB_PASSWORD holds what looks like a real secret (sensitive key name with a value set)
.env:10: error [secret-value] STRIPE_SECRET holds what looks like a real secret (sensitive key name with a value set)
envdoc: 3 errors, 1 warning (1 repo(s), 1 candidate file(s))
```

It reports env files git already **tracks** (leaked: rotate the secrets
first), files it does **not ignore**, unignored `*.sql` and `*.dump` files,
and repositories whose `.gitignore` has no `.env` rule. Findings name keys
and reasons, never values.

### `envdoc hook`

```bash
envdoc hook install     # respects core.hooksPath
envdoc hook uninstall   # only removes a hook envdoc wrote
```

The hook blocks a commit that adds an env file, a database dump, or an
example file containing a secret, including files forced past `.gitignore`:

```console
$ git add -f .env && git commit -m "add config"
.env: error [staged-env-file] env file is staged for commit (it is ignored but was force-added) - unstage it with 'git restore --staged .env'

envdoc: commit blocked (1 problem(s) in 1 staged file(s)). Bypass once with 'git commit --no-verify'.
```

It reads the **staged** content, not the file on disk, so staging a leak
and then cleaning the working copy does not fool it.

Drift warns but does not block. A hook that refuses commits over
documentation is a hook people delete. `--strict` makes it block anyway.

With [pre-commit](https://pre-commit.com):

```yaml
- repo: https://github.com/hassixmalik/envdoc
  rev: v0.1.0
  hooks:
    - id: envdoc
```

## How it reads your code

| Language | Required (no fallback) | Optional |
|---|---|---|
| PHP / Laravel | `env('X')`, `getenv('X')`, `$_ENV['X']` | `env('X', 'default')` |
| JS / TS / Vue | `process.env.X`, `import.meta.env.X` | `process.env.X ?? 'd'`, `process.env.X \|\| 'd'` |
| Python | `os.environ['X']` | `os.environ.get('X')`, `os.getenv('X')` |

**Laravel needs special handling.** Stock Laravel config writes
`env('AWS_ACCESS_KEY_ID')` with no default on purpose, because `null` means
"this driver is not in use". Taken literally, that flags dozens of keys on
a brand-new app. Laravel 11+ ships its stock config in
`vendor/laravel/framework/config`, so envdoc reads that at runtime and
treats those keys as optional. The list always matches the version you
actually have installed.

## Using it in CI

```yaml
- run: pip install git+https://github.com/hassixmalik/envdoc.git
- run: envdoc check --format github
```

## Limitations

- **Pattern matching, not parsing.** Dynamic names such as
  `env($prefix.'_HOST')` are invisible, and a reference inside a comment
  still counts. That is fine for catching drift. Do not treat it as a
  security boundary.
- **Secret detection is heuristic.** Known token formats (AWS, GitHub,
  Stripe, Slack, JWTs, private keys and others) are matched exactly.
  Entropy is a fallback, limited to token-shaped values. It will miss some
  secrets and occasionally flag a non-secret.
- **`--unused` is noisy on frameworks.** Frameworks read variables from
  `vendor/`, which is not scanned, so keys like `PHP_CLI_SERVER_WORKERS`
  look unused. That is why it is opt-in.
- **The hook only sees commits.** It cannot help with a secret that is
  already in history. `envdoc scan` reports tracked files, but rotating
  the credential is on you.

## License

MIT
