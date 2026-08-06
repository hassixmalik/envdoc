import shutil
import subprocess

import pytest

from envdoc.cli import main
from envdoc.hook import MARKER
from envdoc.init import init

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    return tmp_path


def ignored(repo, name):
    return subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", name], check=False
    ).returncode == 0


class TestInit:
    def test_sets_up_a_bare_repo(self, repo):
        (repo / ".env").write_text("APP_ENV=local\nDB_PASSWORD=hunter2\n")
        r = init(repo)

        assert ignored(repo, ".env")
        assert ignored(repo, ".env.production")
        assert not ignored(repo, ".env.example"), "the example must stay committable"

        example = (repo / ".env.example").read_text()
        assert "APP_ENV=local" in example
        assert "DB_PASSWORD=\n" in example and "hunter2" not in example

        assert MARKER in (repo / ".git" / "hooks" / "pre-commit").read_text()
        assert len(r.done) == 3 and not r.problems

    def test_is_idempotent(self, repo):
        (repo / ".env").write_text("A=1\n")
        init(repo)
        gitignore = (repo / ".gitignore").read_text()
        second = init(repo)
        assert (repo / ".gitignore").read_text() == gitignore
        assert second.done == [], "a second run must change nothing"
        assert any("already covers" in s for s in second.skipped)
        assert any("hook already installed" in s for s in second.skipped)
        assert any("already exists" in s for s in second.skipped)

    def test_appends_to_an_existing_gitignore(self, repo):
        (repo / ".gitignore").write_text("node_modules/")  # no trailing newline
        init(repo, hook=False)
        text = (repo / ".gitignore").read_text()
        assert text.startswith("node_modules/\n")
        assert ".env\n" in text

    def test_does_not_overwrite_an_existing_example(self, repo):
        (repo / ".env").write_text("A=1\nB=2\n")
        (repo / ".env.example").write_text("A=\n")
        init(repo, hook=False)
        assert (repo / ".env.example").read_text() == "A=\n"

    def test_warns_when_a_broad_rule_hides_the_example(self, repo):
        (repo / ".gitignore").write_text(".env*\n")
        r = init(repo, hook=False)
        assert any(".env.example is ignored" in p for p in r.problems)

    def test_reports_a_foreign_hook_instead_of_failing(self, repo):
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\nnpm test\n")
        r = init(repo)
        assert any("not written by envdoc" in p for p in r.problems)
        assert "npm test" in (hooks / "pre-commit").read_text()

    def test_cli(self, repo, capsys):
        (repo / ".env").write_text("A=1\n")
        assert main(["init", "-C", str(repo), "--no-hook"]) == 0
        out = capsys.readouterr().out
        assert "added .env rules" in out and "created .env.example" in out

    def test_cli_exit_code_reflects_problems(self, repo):
        (repo / ".gitignore").write_text(".env*\n")
        assert main(["init", "-C", str(repo), "--no-hook"]) == 1
