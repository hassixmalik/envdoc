import json
import shutil
import subprocess

import pytest

from envdoc.cli import main

FAKE_APP_KEY = "base64:" + "kQ3Zt0v9cXnRrJ7wq1yPpLmA2sD4fG6hJ8kL0zX1cV3="


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".env").write_text(
        f"APP_ENV=local\nAPP_KEY={FAKE_APP_KEY}\nDB_HOST=127.0.0.1\nDB_PASSWORD=hunter2\n"
    )
    (tmp_path / ".env.example").write_text("APP_ENV=local\nAPP_KEY=\n")
    return tmp_path


class TestCheck:
    def test_reports_drift_and_fails(self, project, capsys):
        assert main(["check", "-C", str(project)]) == 1
        out = capsys.readouterr()
        assert "missing-in-example" in out.out
        assert "DB_PASSWORD" in out.out
        assert "envdoc: 2 errors" in out.err

    def test_clean_project_passes(self, tmp_path, capsys):
        (tmp_path / ".env").write_text("APP_ENV=local\n")
        (tmp_path / ".env.example").write_text("APP_ENV=\n")
        assert main(["check", "-C", str(tmp_path)]) == 0
        assert "all clear" in capsys.readouterr().err

    def test_never_prints_secret_values(self, project, capsys):
        main(["check", "-C", str(project)])
        out = capsys.readouterr()
        assert "hunter2" not in out.out + out.err
        assert FAKE_APP_KEY not in out.out + out.err

    def test_missing_example_points_at_sync(self, tmp_path, capsys):
        (tmp_path / ".env").write_text("A=1\n")
        assert main(["check", "-C", str(tmp_path)]) == 1
        assert "envdoc sync" in capsys.readouterr().err

    def test_runs_without_env_like_on_ci(self, tmp_path, capsys):
        (tmp_path / ".env.example").write_text("APP_ENV=\n")
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "billing.php").write_text("<?php env('BILLING_SECRET');")
        assert main(["check", "-C", str(tmp_path)]) == 1
        out = capsys.readouterr()
        assert "checking .env.example and code only" in out.err
        assert "undocumented-required" in out.out

    def test_leaked_secret_in_example_fails(self, tmp_path, capsys):
        (tmp_path / ".env.example").write_text(f"APP_KEY={FAKE_APP_KEY}\n")
        assert main(["check", "-C", str(tmp_path), "--no-code"]) == 1
        assert "secret-in-example" in capsys.readouterr().out

    def test_ignore_flag_and_file(self, project):
        assert main(["check", "-C", str(project), "--ignore", "DB_HOST",
                     "--ignore", "DB_PASSWORD"]) == 0
        (project / ".envdocignore").write_text("# local only\nDB_HOST\nDB_PASSWORD\n")
        assert main(["check", "-C", str(project)]) == 0

    def test_skip_suppresses_a_code(self, project):
        assert main(["check", "-C", str(project), "--skip", "missing-in-example"]) == 0

    def test_strict_fails_on_warnings(self, tmp_path):
        (tmp_path / ".env").write_text("A=1\n")
        (tmp_path / ".env.example").write_text("A=\nOPTIONAL=\n")
        assert main(["check", "-C", str(tmp_path)]) == 0
        assert main(["check", "-C", str(tmp_path), "--strict"]) == 1

    def test_json_format(self, project, capsys):
        main(["check", "-C", str(project), "--format", "json"])
        data = json.loads(capsys.readouterr().out)
        assert {d["code"] for d in data} == {"missing-in-example"}
        assert data[0]["line"] is not None

    def test_github_format_emits_annotations(self, project, capsys):
        main(["check", "-C", str(project), "--format", "github"])
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("::error file=")
        assert ",line=" in lines[0]

    def test_unparseable_file_exits_2(self, tmp_path, capsys):
        (tmp_path / ".env.example").write_bytes(b"\xff\xfe\x00")
        assert main(["check", "-C", str(tmp_path)]) == 2
        assert "not UTF-8" in capsys.readouterr().err


class TestSync:
    def test_writes_example_without_secrets(self, project, capsys):
        assert main(["sync", "-C", str(project)]) == 0
        text = (project / ".env.example").read_text()
        assert "DB_HOST=127.0.0.1" in text
        assert "DB_PASSWORD=\n" in text
        assert "hunter2" not in text
        assert "updated" in capsys.readouterr().out
        # And now check is clean.
        assert main(["check", "-C", str(project), "--no-code"]) == 0

    def test_dry_run_writes_nothing(self, project, capsys):
        before = (project / ".env.example").read_text()
        assert main(["sync", "-C", str(project), "--dry-run"]) == 0
        assert (project / ".env.example").read_text() == before
        out = capsys.readouterr().out
        assert "+DB_PASSWORD=" in out
        assert "dry run" in out

    def test_creates_example_when_missing(self, tmp_path):
        (tmp_path / ".env").write_text("APP_ENV=local\nSECRET_TOKEN=abc123def456\n")
        assert main(["sync", "-C", str(tmp_path)]) == 0
        text = (tmp_path / ".env.example").read_text()
        assert "APP_ENV=local" in text and "SECRET_TOKEN=\n" in text

    def test_up_to_date(self, tmp_path, capsys):
        (tmp_path / ".env").write_text("A=1\n")
        (tmp_path / ".env.example").write_text("A=\n")
        assert main(["sync", "-C", str(tmp_path)]) == 0
        assert "already up to date" in capsys.readouterr().out

    def test_no_env_to_sync_from(self, tmp_path, capsys):
        assert main(["sync", "-C", str(tmp_path)]) == 1
        assert "nothing to sync from" in capsys.readouterr().err

    def test_preserves_crlf_on_disk(self, tmp_path):
        (tmp_path / ".env").write_bytes(b"DB_A=1\r\nDB_B=2\r\n")
        (tmp_path / ".env.example").write_bytes(b"DB_A=\r\n")
        main(["sync", "-C", str(tmp_path)])
        assert (tmp_path / ".env.example").read_bytes() == b"DB_A=\r\nDB_B=2\r\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
class TestScan:
    def test_scan_reports_and_exits(self, tmp_path, capsys):
        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
        (tmp_path / ".gitignore").write_text("node_modules/\n")
        (tmp_path / ".env").write_text("DB_PASSWORD=hunter2\n")
        assert main(["scan", str(tmp_path)]) == 1
        out = capsys.readouterr()
        assert "unignored-env-file" in out.out
        assert "1 repo(s)" in out.err


class TestTopLevel:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--version"])
        assert e.value.code == 0
        assert "envdoc" in capsys.readouterr().out

    def test_subcommand_required(self):
        with pytest.raises(SystemExit):
            main([])
