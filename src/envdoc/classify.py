"""Decide whether a value is a secret, and whether it is safe to publish.

Two questions, deliberately kept separate:

``secret_reason(key, value)``
    Does this look like a credential? Used to catch real secrets that
    leaked into ``.env.example`` and to drive ``envdoc scan``. Returns a
    human-readable reason, or ``None``.

``publishable(key, value)``
    Can this value appear in a committed example file? Stricter: a value
    can be non-secret and still not worth publishing (a teammate's
    hostname, a personal email). Only boring, generic values pass -
    ``local``, ``true``, ``3306``, ``127.0.0.1`` - and everything else is
    blanked by ``sync``.

The pattern list favours tokens that announce themselves (fixed prefixes,
PEM headers). Entropy is the fallback and is restricted to token-like
character sets, because prose, URLs and filesystem paths all score high
enough to trip a naive threshold.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Optional, Pattern, Tuple

__all__ = ["entropy", "publishable", "secret_key_name", "secret_reason"]

# (name, compiled pattern) - matched against the whole value.
_TOKEN_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("Stripe secret key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("SendGrid key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b")),
    ("Twilio key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("Laravel app key", re.compile(r"^base64:[A-Za-z0-9+/]{32,}={0,2}$")),
    ("credentials in URL", re.compile(r"^[a-z][a-z0-9+.\-]*://[^:/@\s]+:[^@/\s]+@", re.I)),
]

# Words that, appearing as a segment of the key name, mark it sensitive.
_SECRET_NAME_PARTS = {
    "PASSWORD", "PASSWD", "PASS", "PWD", "SECRET", "TOKEN", "KEY", "APIKEY",
    "PRIVATE", "CREDENTIAL", "CREDENTIALS", "AUTH", "DSN", "SALT", "CERT",
    "SIGNATURE", "SESSION", "COOKIE", "WEBHOOK",
}
# Key names that contain a sensitive word but are routinely harmless.
_NAME_ALLOWLIST = {
    "PASSWORD_TIMEOUT", "AUTH_GUARD", "AUTH_MODEL", "AUTH_PASSWORD_BROKER",
    "AUTH_PASSWORD_RESET_TOKEN_TABLE", "SESSION_DRIVER", "SESSION_LIFETIME",
    "SESSION_ENCRYPT", "SESSION_PATH", "SESSION_DOMAIN", "SESSION_SECURE_COOKIE",
    "SESSION_SAME_SITE", "SESSION_HTTP_ONLY", "SESSION_PARTITIONED_COOKIE",
    "SESSION_EXPIRE_ON_CLOSE", "SESSION_TABLE", "SESSION_CONNECTION",
    "SESSION_STORE", "CACHE_PREFIX_KEY",
}

# Values generic enough to publish in an example file.
_PUBLISHABLE_WORDS = {
    "true", "false", "null", "none", "yes", "no", "on", "off",
    "local", "localhost", "development", "dev", "testing", "test",
    "staging", "production", "prod",
    "debug", "info", "notice", "warning", "error", "critical",
    "stack", "single", "daily", "stderr", "syslog", "errorlog",
    "sqlite", "mysql", "mariadb", "pgsql", "postgres", "postgresql", "sqlsrv",
    "redis", "memcached", "database", "file", "array", "cookie", "apc",
    "sync", "log", "smtp", "sendmail", "mailgun", "ses", "postmark", "resend",
    "public", "private", "s3", "phpredis", "predis", "lax", "strict",
    "utf8mb4", "utf8mb4_unicode_ci", "tls", "ssl", "bcrypt", "argon2id",
    "en", "en_us", "utc",
}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
_PLACEHOLDER_RE = re.compile(
    r"^(?:<[^>]+>|\$\{[^}]+\}|changeme|change[-_]me|your[-_].*|xxx+|\*+|example(?:\.com)?)$",
    re.I,
)
_TOKEN_CHARSET_RE = re.compile(r"^[A-Za-z0-9+/=_\-.]+$")


def entropy(value: str) -> float:
    """Shannon entropy in bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def secret_key_name(key: str) -> bool:
    """Does the variable *name* suggest it holds a secret?"""
    upper = key.upper()
    if upper in _NAME_ALLOWLIST:
        return False
    parts = set(re.split(r"[_.\-]+", upper))
    return bool(parts & _SECRET_NAME_PARTS)


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def secret_reason(key: str, value: str) -> Optional[str]:
    """Why ``value`` looks like a real credential, or ``None``.

    Empty values and obvious placeholders never count - an example file
    full of ``DB_PASSWORD=`` and ``API_KEY=<your-key>`` is exactly right.
    """
    v = value.strip()
    if not v or _is_placeholder(v):
        return None

    for name, pattern in _TOKEN_PATTERNS:
        if pattern.search(v):
            return name

    # Entropy only for token-shaped values; URLs, paths and prose score
    # high too and would drown the report in false positives.
    if len(v) >= 20 and _TOKEN_CHARSET_RE.match(v):
        has_digit = any(c.isdigit() for c in v)
        has_alpha = any(c.isalpha() for c in v)
        if has_digit and has_alpha and entropy(v) >= 3.5:
            return f"high-entropy string ({entropy(v):.1f} bits/char)"

    # A sensitive name with a non-trivial value is worth flagging even when
    # the value itself is unremarkable - 'hunter2' is still a password.
    if secret_key_name(key) and v.lower() not in _PUBLISHABLE_WORDS and len(v) >= 4:
        return f"value set for sensitive-looking key {key}"

    return None


def publishable(key: str, value: str) -> bool:
    """Is this value generic enough to commit in ``.env.example``?"""
    v = value.strip()
    if not v:
        return True
    if secret_key_name(key) or secret_reason(key, v):
        return False
    if v.lower() in _PUBLISHABLE_WORDS or v in _LOCAL_HOSTS or _is_placeholder(v):
        return True
    # Small integers: ports, lifetimes, worker counts.
    if v.isdigit() and len(v) <= 6:
        return True
    # Local URLs such as http://localhost:8000.
    if re.match(r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?/?$", v):
        return True
    # References to other variables: MAIL_FROM_NAME="${APP_NAME}".
    if re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", v):
        return True
    return False
