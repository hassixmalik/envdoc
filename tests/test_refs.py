from pathlib import Path

import pytest

from envdoc.parser import parse
from envdoc.refs import code_findings, find_references, scan_text


def keys(refs):
    return [(r.key, r.required) for r in refs]


class TestPHP:
    def test_laravel_env_with_default_is_optional(self):
        assert keys(scan_text("'host' => env('DB_HOST', '127.0.0.1'),", "php")) == [
            ("DB_HOST", False)
        ]

    def test_laravel_env_without_default_is_required(self):
        assert keys(scan_text("'secret' => env('STRIPE_SECRET'),", "php")) == [
            ("STRIPE_SECRET", True)
        ]

    def test_double_quotes_and_spacing(self):
        assert keys(scan_text('env( "APP_KEY" )', "php")) == [("APP_KEY", True)]

    def test_method_calls_named_env_are_ignored(self):
        src = "$app->env('X'); Foo::env('Y'); $this->env('Z');"
        assert scan_text(src, "php") == []

    def test_getenv_and_superglobal(self):
        src = "getenv('A'); $_ENV['B'];"
        assert keys(scan_text(src, "php")) == [("A", True), ("B", True)]

    def test_line_numbers(self):
        src = "<?php\n\nreturn [\n    'k' => env('LINE_FOUR'),\n];\n"
        assert scan_text(src, "php")[0].line == 4


class TestPython:
    def test_subscript_is_required(self):
        assert keys(scan_text("os.environ['DATABASE_URL']", "python")) == [
            ("DATABASE_URL", True)
        ]

    @pytest.mark.parametrize(
        "src", ["os.environ.get('X')", "os.environ.get('X', 'd')", "os.getenv('X')"]
    )
    def test_get_and_getenv_are_optional(self, src):
        assert keys(scan_text(src, "python")) == [("X", False)]


class TestJS:
    def test_bare_process_env_is_required(self):
        assert keys(scan_text("const k = process.env.API_KEY;", "js")) == [("API_KEY", True)]

    @pytest.mark.parametrize(
        "src",
        ["process.env.PORT ?? 3000", "process.env.PORT || 3000", "process.env['PORT'] || 3000"],
    )
    def test_fallback_operators_make_it_optional(self, src):
        assert keys(scan_text(src, "js")) == [("PORT", False)]

    def test_vite_import_meta(self):
        assert keys(scan_text("import.meta.env.VITE_APP_NAME", "js")) == [
            ("VITE_APP_NAME", True)
        ]

    def test_toolchain_builtins_are_skipped(self):
        src = "import.meta.env.DEV; import.meta.env.MODE; process.env.NODE_ENV"
        assert scan_text(src, "js") == []


class TestFindReferences:
    def test_walks_a_project_and_skips_vendor(self, tmp_path):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "app.php").write_text("<?php env('APP_NAME', 'x');")
        (tmp_path / "vendor" / "pkg").mkdir(parents=True)
        (tmp_path / "vendor" / "pkg" / "x.php").write_text("<?php env('VENDOR_ONLY');")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.ts").write_text("process.env.API_URL")
        (tmp_path / "notes.txt").write_text("env('NOT_CODE')")

        found = sorted(r.key for r in find_references(tmp_path))
        assert found == ["API_URL", "APP_NAME"]


class TestCodeFindings:
    def _example(self, text):
        ex = parse(text)
        ex.path = Path(".env.example")
        return ex

    def _refs(self, src, lang="php"):
        return scan_text(src, lang, Path("config/x.php"))

    def test_required_and_undocumented_is_an_error(self):
        refs = self._refs("env('STRIPE_SECRET'); env('APP_NAME', 'x');")
        out = code_findings(refs, self._example("APP_NAME=\n"))
        assert [(f.code, f.key, f.severity) for f in out] == [
            ("undocumented-required", "STRIPE_SECRET", "error")
        ]
        assert out[0].location() == "config/x.php:1"

    def test_optional_and_undocumented_is_quiet_by_default(self):
        refs = self._refs("env('REDIS_HOST', '127.0.0.1');")
        assert code_findings(refs, self._example("")) == []

    def test_optional_can_be_requested(self):
        refs = self._refs("env('REDIS_HOST', '127.0.0.1');")
        out = code_findings(refs, self._example(""), include_optional=True)
        assert [f.code for f in out] == ["undocumented-optional"]

    def test_any_required_use_makes_the_key_required(self):
        refs = self._refs("env('X', 1);\nenv('X');")
        out = code_findings(refs, self._example(""))
        assert out[0].code == "undocumented-required"
        assert out[0].line == 2
        assert "(+1 more)" in out[0].message

    def test_documented_keys_are_fine(self):
        refs = self._refs("env('STRIPE_SECRET');")
        assert code_findings(refs, self._example("STRIPE_SECRET=\n")) == []

    def test_unused_is_opt_in(self):
        ex = self._example("USED=\nSTALE=\n")
        refs = self._refs("env('USED');")
        assert code_findings(refs, ex) == []
        out = code_findings(refs, ex, include_unused=True)
        assert [(f.code, f.key) for f in out] == [("unused-key", "STALE")]

    def test_ignore_keys(self):
        refs = self._refs("env('INTERNAL');")
        assert code_findings(refs, self._example(""), ignore_keys=["INTERNAL"]) == []


class TestFrameworkKeys:
    def _project(self, tmp_path):
        stock = tmp_path / "vendor" / "laravel" / "framework" / "config"
        stock.mkdir(parents=True)
        (stock / "filesystems.php").write_text(
            "<?php return ['key' => env('AWS_ACCESS_KEY_ID'), 'b' => env('AWS_BUCKET')];"
        )
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "filesystems.php").write_text(
            "<?php return ['key' => env('AWS_ACCESS_KEY_ID'), 'b' => env('AWS_BUCKET')];"
        )
        (tmp_path / "config" / "billing.php").write_text(
            "<?php return ['secret' => env('BILLING_SECRET')];"
        )
        return tmp_path

    def test_reads_stock_config_from_vendor(self, tmp_path):
        from envdoc.refs import framework_keys

        assert framework_keys(self._project(tmp_path)) == {"AWS_ACCESS_KEY_ID", "AWS_BUCKET"}

    def test_no_framework_installed(self, tmp_path):
        from envdoc.refs import framework_keys

        assert framework_keys(tmp_path) == set()

    def test_stock_keys_are_not_errors_but_custom_ones_are(self, tmp_path):
        from envdoc.refs import framework_keys

        root = self._project(tmp_path)
        refs = find_references(root)
        ex = parse("")
        ex.path = Path(".env.example")

        naive = code_findings(refs, ex)
        assert sorted(f.key for f in naive) == ["AWS_ACCESS_KEY_ID", "AWS_BUCKET", "BILLING_SECRET"]

        aware = code_findings(refs, ex, framework=framework_keys(root))
        assert [(f.code, f.key) for f in aware] == [("undocumented-required", "BILLING_SECRET")]

        # Still visible when asked for.
        everything = code_findings(
            refs, ex, framework=framework_keys(root), include_optional=True
        )
        assert {f.key for f in everything} == {"AWS_ACCESS_KEY_ID", "AWS_BUCKET", "BILLING_SECRET"}
