from pathlib import Path

from envdoc.check import ERROR, WARNING, Finding, Report, compare
from envdoc.parser import parse


def check(env_text, example_text, **kw):
    env = parse(env_text)
    env.path = Path(".env")
    ex = parse(example_text)
    ex.path = Path(".env.example")
    return compare(env, ex, **kw)


class TestCompare:
    def test_in_sync_is_clean(self):
        r = check("A=1\nB=2\n", "A=\nB=\n")
        assert r.findings == []
        assert r.ok

    def test_key_missing_from_example_is_an_error(self):
        r = check("A=1\nSECRET=x\n", "A=\n")
        assert r.codes() == ["missing-in-example"]
        assert r.errors[0].key == "SECRET"
        assert not r.ok

    def test_points_at_the_line_in_env(self):
        r = check("A=1\n\n# db\nDB_HOST=x\n", "A=\n")
        assert r.errors[0].line == 4
        assert r.errors[0].location() == ".env:4"

    def test_key_missing_from_env_is_only_a_warning(self):
        r = check("A=1\n", "A=\nOPTIONAL=\n")
        assert r.codes() == ["missing-in-env"]
        assert r.warnings[0].key == "OPTIONAL"
        assert r.ok, "a warning alone must not fail the check"

    def test_reproduces_the_nmr_drift(self):
        """The real case that motivated the tool: DB creds never documented."""
        env = (
            "APP_NAME=nmr\n"
            "DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_DATABASE=nmr\n"
            "DB_USERNAME=root\nDB_PASSWORD=secret\n"
            "LEGACY_DB_HOST=x\nLEGACY_DB_PASSWORD=y\n"
        )
        r = check(env, "APP_NAME=\n")
        missing = sorted(f.key for f in r.errors)
        assert missing == [
            "DB_DATABASE", "DB_HOST", "DB_PASSWORD", "DB_PORT", "DB_USERNAME",
            "LEGACY_DB_HOST", "LEGACY_DB_PASSWORD",
        ]

    def test_ignore_keys_suppresses_both_directions(self):
        r = check("LOCAL_ONLY=1\n", "CI_ONLY=\n", ignore_keys=["LOCAL_ONLY", "CI_ONLY"])
        assert r.findings == []

    def test_duplicates_are_reported_per_file(self):
        r = check("A=1\nA=2\n", "A=\n")
        dup = [f for f in r.findings if f.code == "duplicate-key"]
        assert len(dup) == 1
        assert dup[0].severity == WARNING
        assert "lines 1, 2" in dup[0].message
        assert dup[0].line == 2

    def test_invalid_lines_are_errors(self):
        r = check("A=1\nnot valid\n", "A=\n")
        assert r.codes() == ["invalid-line"]
        assert r.errors[0].line == 2

    def test_invalid_line_in_example_is_reported_against_example(self):
        r = check("A=1\n", "A=\n1BAD=x\n")
        f = r.errors[0]
        assert f.code == "invalid-line"
        assert f.path == Path(".env.example")

    def test_values_do_not_matter_only_keys(self):
        r = check("A=production\n", "A=local\n")
        assert r.findings == []


class TestReport:
    def _report(self):
        return Report(
            [
                Finding("missing-in-example", ERROR, "x", key="A"),
                Finding("missing-in-env", WARNING, "y", key="B"),
            ]
        )

    def test_without_drops_codes(self):
        r = self._report().without(["missing-in-example"])
        assert r.codes() == ["missing-in-env"]
        assert r.ok

    def test_promote_warnings_makes_them_fail(self):
        r = Report([Finding("missing-in-env", WARNING, "y")])
        assert r.ok
        assert not r.promote_warnings().ok

    def test_format_includes_location_and_code(self):
        f = Finding("missing-in-example", ERROR, "msg", path=Path(".env"), line=3)
        assert f.format() == ".env:3: error [missing-in-example] msg"

    def test_format_without_location(self):
        assert Finding("x", WARNING, "msg").format() == "warning [x] msg"
