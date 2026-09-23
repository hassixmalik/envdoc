import os
import shutil
import subprocess
from pathlib import Path

import pytest

from envdoc.scan import is_dump_file, is_env_file, scan

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

FAKE_APP_KEY = "base64:" + "kQ3Zt0v9cXnRrJ7wq1yPpLmA2sD4fG6hJ8kL0zX1cV3="


# Inherit the real environment: a hardcoded Unix PATH cannot find git on
# a Windows runner.
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, env=GIT_ENV
    )


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q")
    return r


def codes(result):
    return sorted(f.code for f in result.findings)


class TestNaming:
    @pytest.mark.parametrize(
        "name",
        [".env", ".env.local", ".env.production", "server.info.env", "credentials.env"],
    )
    def test_env_files(self, name):
        assert is_env_file(Path(name))

    @pytest.mark.parametrize(
        "name",
        [".env.example", ".env.sample", ".env.dist", "app.env.template", "README.md", "envdoc.py"],
    )
    def test_not_env_files(self, name):
        assert not is_env_file(Path(name))

    @pytest.mark.parametrize("name", ["dump.sql", "backup.sql.gz", "prod.dump", "db.bak"])
    def test_dumps(self, name):
        assert is_dump_file(Path(name))


class TestScan:
    def test_clean_repo(self, repo):
        (repo / ".gitignore").write_text(".env\n")
        (repo / ".env").write_text(f"APP_KEY={FAKE_APP_KEY}\n")
        (repo / ".env.example").write_text("APP_KEY=\n")
        r = scan(repo)
        assert r.findings == []
        assert r.ok

    def test_gitignore_without_env_rule(self, repo):
        (repo / ".gitignore").write_text("node_modules/\n")
        r = scan(repo)
        assert codes(r) == ["gitignore-missing-env"]
        assert r.ok, "preventive: nothing has leaked yet"

    def test_missing_rule_plus_real_env_file_fails(self, repo):
        (repo / ".gitignore").write_text("node_modules/\n")
        (repo / ".env").write_text("APP_ENV=local\n")
        r = scan(repo)
        assert codes(r) == ["gitignore-missing-env", "unignored-env-file"]
        assert not r.ok

    def test_unignored_env_file_and_its_secrets(self, repo):
        (repo / ".gitignore").write_text(".env\n")
        (repo / ".env.production").write_text(f"APP_KEY={FAKE_APP_KEY}\nAPP_ENV=production\n")
        r = scan(repo)
        assert codes(r) == ["secret-value", "unignored-env-file"]
        secret = next(f for f in r.findings if f.code == "secret-value")
        assert secret.key == "APP_KEY" and secret.line == 1

    def test_tracked_env_file_is_reported_even_if_now_ignored(self, repo):
        (repo / ".env").write_text("DB_PASSWORD=hunter2\n")
        git(repo, "add", ".env")
        git(repo, "commit", "-qm", "oops")
        (repo / ".gitignore").write_text(".env\n")
        r = scan(repo)
        assert "tracked-env-file" in codes(r)
        assert "secret-value" in codes(r)

    def test_unignored_dump_is_a_warning(self, repo):
        (repo / ".gitignore").write_text(".env\n")
        (repo / "prod.sql").write_text("INSERT INTO users VALUES (1);\n")
        r = scan(repo)
        assert codes(r) == ["unignored-dump"]
        assert r.ok, "a dump alone is a warning, not a failure"

    def test_ignored_dump_is_fine(self, repo):
        (repo / ".gitignore").write_text(".env\n*.sql\n")
        (repo / "prod.sql").write_text("x\n")
        assert scan(repo).findings == []

    def test_example_files_are_never_flagged_as_env_files(self, repo):
        (repo / ".gitignore").write_text(".env\n")
        (repo / ".env.example").write_text("APP_KEY=\n")
        assert scan(repo).findings == []

    def test_skips_vendor_and_node_modules(self, repo):
        (repo / ".gitignore").write_text(".env\n")
        for d in ("vendor/pkg", "node_modules/pkg"):
            (repo / d).mkdir(parents=True)
            (repo / d / ".env").write_text("DB_PASSWORD=hunter2\n")
        assert scan(repo).findings == []

    def test_loose_secret_file_outside_any_repo(self, tmp_path):
        loose = tmp_path / "workspace"
        loose.mkdir()
        (loose / "server.info.env").write_text("SERVER_PASSWORD=Sup3rS3cret!\n")
        r = scan(loose)
        assert codes(r) == ["secret-value"]
        assert r.findings[0].severity == "warning"
        assert r.ok

    def test_finds_nested_repositories(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        for name in ("one", "two"):
            (ws / name).mkdir()
            git(ws / name, "init", "-q")
        (ws / "one" / ".gitignore").write_text(".env\n")
        r = scan(ws)
        assert len(r.repos) == 2
        bad = [f for f in r.findings if f.code == "gitignore-missing-env"]
        assert len(bad) == 1 and bad[0].path.parent.name == "two"
