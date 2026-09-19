from envdoc.parser import parse
from envdoc.sync import APPENDED_MARKER, sync

FAKE_APP_KEY = "base64:" + "kQ3Zt0v9cXnRrJ7wq1yPpLmA2sD4fG6hJ8kL0zX1cV3="


def run(env_text, example_text=None, **kw):
    example = parse(example_text) if example_text is not None else None
    return sync(parse(env_text), example, **kw)


class TestAdding:
    def test_in_sync_is_a_no_op(self):
        r = run("A=1\n", "A=\n")
        assert not r.changed
        assert r.text == "A=\n"

    def test_secret_values_are_withheld(self):
        r = run("DB_PASSWORD=hunter2\n", "")
        assert "DB_PASSWORD=\n" in r.text
        assert "hunter2" not in r.text
        assert r.changes[0].action == "added-blank"

    def test_generic_values_are_kept(self):
        r = run("APP_ENV=local\nDB_PORT=3306\n", "")
        assert "APP_ENV=local\n" in r.text
        assert "DB_PORT=3306\n" in r.text
        assert {c.action for c in r.changes} == {"added"}

    def test_specific_values_are_withheld(self):
        r = run("DB_HOST=prod-db-01.acme.internal\n", "")
        assert "DB_HOST=\n" in r.text

    def test_kept_values_keep_their_quoting(self):
        r = run('MAIL_FROM_NAME="${APP_NAME}"\n', "")
        assert 'MAIL_FROM_NAME="${APP_NAME}"\n' in r.text

    def test_export_prefix_is_preserved_on_blanked_keys(self):
        r = run("export API_TOKEN=abc123xyz\n", "")
        assert "export API_TOKEN=\n" in r.text

    def test_creates_an_example_from_nothing(self):
        r = run("APP_ENV=local\nAPP_KEY=" + FAKE_APP_KEY + "\n")
        assert "APP_ENV=local\n" in r.text
        assert "APP_KEY=\n" in r.text
        assert FAKE_APP_KEY not in r.text


class TestPlacement:
    def test_new_key_joins_its_family(self):
        example = "APP_NAME=\n\nDB_HOST=\nDB_PORT=\n\nMAIL_MAILER=\n"
        r = run("APP_NAME=x\nDB_HOST=x\nDB_PORT=1\nDB_SSLMODE=require\nMAIL_MAILER=log\n", example)
        lines = r.text.splitlines()
        assert lines.index("DB_SSLMODE=require") == lines.index("DB_PORT=") + 1
        assert APPENDED_MARKER not in r.text

    def test_unrelated_key_goes_to_a_marked_tail(self):
        r = run("APP_NAME=x\nSTRIPE_SECRET=sk\n", "APP_NAME=\n")
        assert r.text.endswith(f"\n{APPENDED_MARKER}\nSTRIPE_SECRET=\n")

    def test_several_new_keys_keep_env_order(self):
        r = run("DB_A=1\nDB_B=2\nDB_C=3\n", "DB_A=\n")
        assert r.text == "DB_A=\nDB_B=2\nDB_C=3\n"

    def test_reproduces_the_nmr_fix(self):
        example = "APP_NAME=Laravel\nAPP_ENV=local\n\nDB_CONNECTION=sqlite\n"
        env = (
            "APP_NAME=nmr\nAPP_ENV=local\n\nDB_CONNECTION=mysql\n"
            "DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_DATABASE=nmr_v2\n"
            "DB_USERNAME=u712280863_nmr\nDB_PASSWORD=s3cret!\n"
            "LEGACY_DB_HOST=127.0.0.1\nLEGACY_DB_PASSWORD=s3cret!\n"
        )
        r = run(env, example)
        assert "s3cret" not in r.text
        assert "u712280863" not in r.text
        # DB_* slot in under DB_CONNECTION; LEGACY_* has no family yet.
        lines = r.text.splitlines()
        assert lines[lines.index("DB_CONNECTION=sqlite") + 1] == "DB_HOST=127.0.0.1"
        assert "DB_PORT=3306" in lines
        assert lines.index(APPENDED_MARKER) < lines.index("LEGACY_DB_HOST=127.0.0.1")


class TestPreservation:
    def test_comments_blanks_and_order_survive(self):
        example = "# App settings\nAPP_NAME=\n\n# Database\nDB_HOST=\n"
        r = run("APP_NAME=x\nDB_HOST=y\n", example)
        assert r.text == example

    def test_invalid_lines_are_never_dropped(self):
        example = "APP_NAME=\nthis is not valid\n"
        r = run("APP_NAME=x\nNEW_KEY=x\n", example)
        assert "this is not valid\n" in r.text

    def test_missing_trailing_newline_is_repaired_before_appending(self):
        r = run("A=1\nZED=2\n", "A=")
        assert r.text.startswith("A=\n")

    def test_crlf_files_stay_crlf(self):
        r = run("DB_A=1\r\nDB_B=2\r\nOTHER=3\r\n", "DB_A=\r\n")
        assert r.text == f"DB_A=\r\nDB_B=2\r\n\r\n{APPENDED_MARKER}\r\nOTHER=3\r\n"
        assert "\n" not in r.text.replace("\r\n", "")


class TestRedactAndPrune:
    def test_leaked_secret_in_example_is_blanked(self):
        r = run("APP_KEY=x\n", f"APP_KEY={FAKE_APP_KEY}\n")
        assert r.text == "APP_KEY=\n"
        assert r.changes[0].action == "redacted"
        assert "Laravel app key" in r.changes[0].detail

    def test_redact_can_be_disabled(self):
        r = run("APP_KEY=x\n", f"APP_KEY={FAKE_APP_KEY}\n", redact=False)
        assert FAKE_APP_KEY in r.text
        assert not r.changed

    def test_prune_is_off_by_default(self):
        r = run("A=1\n", "A=\nOPTIONAL_FEATURE=\n")
        assert "OPTIONAL_FEATURE=" in r.text

    def test_prune_removes_keys_env_no_longer_sets(self):
        r = run("A=1\n", "A=\nGONE=\n", prune=True)
        assert r.text == "A=\n"
        assert r.changes[0].action == "pruned"

    def test_describe_is_readable(self):
        r = run("DB_PASSWORD=x\n", "")
        assert r.changes[0].describe() == "added (value withheld): DB_PASSWORD"
