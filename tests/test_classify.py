import pytest

from envdoc.classify import entropy, publishable, secret_key_name, secret_reason

# Fake credentials assembled at runtime so the source file itself never
# contains a string that a secret scanner (including this one) would flag.
FAKE_AWS = "AKIA" + "IOSFODNN7" + "EXAMPLE"
FAKE_GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
FAKE_STRIPE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
FAKE_APP_KEY = "base64:" + "kQ3Zt0v9cXnRrJ7wq1yPpLmA2sD4fG6hJ8kL0zX1cV3="
FAKE_PEM = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"


class TestEntropy:
    def test_empty(self):
        assert entropy("") == 0.0

    def test_single_symbol(self):
        assert entropy("aaaa") == 0.0

    def test_two_equal_symbols_is_one_bit(self):
        assert entropy("abab") == pytest.approx(1.0)


class TestSecretKeyName:
    @pytest.mark.parametrize(
        "key",
        ["DB_PASSWORD", "API_KEY", "STRIPE_SECRET", "GITHUB_TOKEN", "APP_KEY",
         "MAIL_PASSWORD", "SENTRY_DSN", "JWT_SECRET", "AWS_SECRET_ACCESS_KEY"],
    )
    def test_sensitive_names(self, key):
        assert secret_key_name(key)

    @pytest.mark.parametrize(
        "key",
        ["APP_NAME", "DB_HOST", "SESSION_DRIVER", "AUTH_GUARD", "PASSWORD_TIMEOUT",
         "SESSION_LIFETIME", "LOG_LEVEL", "KEYBOARD_LAYOUT"],
    )
    def test_harmless_names(self, key):
        assert not secret_key_name(key)


class TestSecretReason:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (FAKE_AWS, "AWS access key id"),
            (FAKE_GH, "GitHub token"),
            (FAKE_STRIPE, "Stripe secret key"),
            (FAKE_APP_KEY, "Laravel app key"),
            (FAKE_PEM, "private key"),
            ("mysql://root:hunter2@db:3306/app", "credentials in URL"),
        ],
    )
    def test_known_token_shapes(self, value, expected):
        assert secret_reason("ANY", value) == expected

    def test_high_entropy_token(self):
        reason = secret_reason("SOMETHING", "q8Zr2LxN0vT5mW9yB3kH7jD1sF6gP4cA")
        assert reason and reason.startswith("high-entropy")

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "<your-api-key>", "${APP_NAME}", "changeme", "your_password_here", "xxxx"],
    )
    def test_empty_and_placeholders_are_not_secrets(self, value):
        assert secret_reason("API_KEY", value) is None

    @pytest.mark.parametrize(
        "value",
        [
            "http://localhost:8000",
            "/var/www/html/storage/app/public",
            "Finico ERP",
            "hello@example.com",
            "utf8mb4_unicode_ci",
        ],
    )
    def test_ordinary_values_are_not_secrets(self, value):
        assert secret_reason("APP_URL", value) is None

    def test_weak_password_is_still_a_password(self):
        assert secret_reason("DB_PASSWORD", "hunter2") is not None

    def test_sensitive_key_with_boring_value_is_fine(self):
        assert secret_reason("DB_PASSWORD", "null") is None


class TestPublishable:
    @pytest.mark.parametrize(
        "key,value",
        [
            ("APP_ENV", "local"),
            ("APP_DEBUG", "true"),
            ("DB_CONNECTION", "sqlite"),
            ("DB_HOST", "127.0.0.1"),
            ("DB_PORT", "3306"),
            ("APP_URL", "http://localhost:8000"),
            ("MAIL_FROM_NAME", "${APP_NAME}"),
            ("LOG_LEVEL", "debug"),
            ("APP_KEY", ""),
        ],
    )
    def test_generic_values_can_be_published(self, key, value):
        assert publishable(key, value)

    @pytest.mark.parametrize(
        "key,value",
        [
            ("DB_PASSWORD", "anything"),
            ("APP_KEY", FAKE_APP_KEY),
            ("DB_HOST", "prod-db-01.internal.acme.net"),
            ("MAIL_FROM_ADDRESS", "hassan@realcompany.com"),
            ("APP_NAME", "Client Project Codename"),
            ("STRIPE_KEY", FAKE_STRIPE),
        ],
    )
    def test_specific_or_secret_values_are_withheld(self, key, value):
        assert not publishable(key, value)


@pytest.mark.parametrize("mode", ["require", "verify-full", "disable", "REQUIRED"])
def test_ssl_modes_are_publishable(mode):
    assert publishable("DB_SSLMODE", mode)
