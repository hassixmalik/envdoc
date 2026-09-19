import os
import shutil
import subprocess

import pytest

from envdoc.cli import main
from envdoc.hook import MARKER, HookError, check_staged, install, run, uninstall

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

FAKE_APP_KEY = "base64:" + "kQ3Zt0v9cXnRrJ7wq1yPpLmA2sD4fG6hJ8kL0zX1cV3="
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=check, env=GIT_ENV
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    return tmp_path


def codes(report):
    return sorted(f.code for f in report.findings)


class TestCheckStaged:
    def test_nothing_staged(self, repo):
        report, count = check_staged(repo)
        assert count == 0 and report.ok

    def test_blocks_a_staged_env_file(self, repo):
        (repo / ".env").write_text("APP_ENV=local\n")
        git(repo, "add", ".env")
        report, _ = check_staged(repo)
        assert codes(report) == ["staged-env-file"]
        assert not report.ok

    def test_advice_differs_for_force_added_files(self, repo):
        (repo / ".gitignore").write_text(".env\n")
        (repo / ".env").write_text("A=1\n")
        git(repo, "add", "-f", ".env")
        msg = check_staged(repo)[0].findings[0].message
        assert "force-added" in msg

    def test_advice_for_never_ignored_files(self, repo):
        (repo / ".env").write_text("A=1\n")
        git(repo, "add", ".env")
        assert "add it to .gitignore" in check_staged(repo)[0].findings[0].message

    def test_blocks_a_staged_dump(self, repo):
        (repo / "prod.sql").write_text("x\n")
        git(repo, "add", "prod.sql")
        assert codes(check_staged(repo)[0]) == ["staged-dump"]

    def test_blocks_a_secret_in_the_staged_example(self, repo):
        (repo / ".env.example").write_text(f"APP_KEY={FAKE_APP_KEY}\n")
        git(repo, "add", ".env.example")
        report, _ = check_staged(repo)
        assert codes(report) == ["secret-in-example"]

    def test_reads_the_index_not_the_working_tree(self, repo):
        """Stage a leak, then 'fix' the file without re-staging."""
        ex = repo / ".env.example"
        ex.write_text(f"APP_KEY={FAKE_APP_KEY}\n")
        git(repo, "add", ".env.example")
        ex.write_text("APP_KEY=\n")
        report, _ = check_staged(repo)
        assert codes(report) == ["secret-in-example"], "the index is what gets committed"

    def test_drift_warns_but_does_not_block(self, repo):
        (repo / ".env").write_text("APP_ENV=local\nNEW_KEY=x\n")
        (repo / ".env.example").write_text("APP_ENV=\n")
        git(repo, "add", ".env.example")
        report, _ = check_staged(repo)
        assert codes(report) == ["missing-in-example"]
        assert report.ok

    def test_strict_drift_blocks(self, repo):
        (repo / ".env").write_text("APP_ENV=local\nNEW_KEY=x\n")
        (repo / ".env.example").write_text("APP_ENV=\n")
        git(repo, "add", ".env.example")
        assert not check_staged(repo, strict=True)[0].ok

    def test_ordinary_files_pass(self, repo):
        (repo / "app.py").write_text("print('hi')\n")
        git(repo, "add", "app.py")
        assert check_staged(repo)[0].findings == []

    def test_outside_a_repo_never_blocks(self, tmp_path, capsys):
        outside = tmp_path / "plain"
        outside.mkdir()
        assert run(outside) == 0
        assert "not inside a git repository" in capsys.readouterr().err


class TestInstall:
    def test_install_writes_an_executable_marked_hook(self, repo):
        path = install(repo)
        text = path.read_text()
        assert MARKER in text and "envdoc hook run" in text
        assert os.access(path, os.X_OK)

    def test_reinstall_over_own_hook_is_fine(self, repo):
        install(repo)
        install(repo)

    def test_refuses_to_clobber_a_foreign_hook(self, repo):
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\nnpm test\n")
        with pytest.raises(HookError, match="not written by envdoc"):
            install(repo)
        assert "npm test" in (hooks / "pre-commit").read_text()

    def test_force_replaces_a_foreign_hook(self, repo):
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\nnpm test\n")
        assert MARKER in install(repo, force=True).read_text()

    def test_respects_core_hookspath(self, repo):
        git(repo, "config", "core.hooksPath", ".githooks")
        path = install(repo)
        assert path == repo / ".githooks" / "pre-commit"

    def test_uninstall_removes_only_its_own_hook(self, repo):
        assert uninstall(repo) is None
        path = install(repo)
        assert uninstall(repo) == path and not path.exists()

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nnpm test\n")
        with pytest.raises(HookError):
            uninstall(repo)


class TestEndToEnd:
    def test_installed_hook_blocks_a_real_commit(self, repo):
        envdoc_bin = shutil.which("envdoc")
        if envdoc_bin is None:
            pytest.skip("envdoc not installed on PATH")
        install(repo)
        (repo / ".env").write_text("DB_PASSWORD=hunter2\n")
        git(repo, "add", ".env")
        result = git(repo, "commit", "-qm", "oops", check=False)
        assert result.returncode != 0
        assert b"commit blocked" in result.stderr
        assert git(repo, "rev-parse", "HEAD", check=False).returncode != 0

    def test_clean_commit_goes_through(self, repo):
        if shutil.which("envdoc") is None:
            pytest.skip("envdoc not installed on PATH")
        install(repo)
        (repo / "README.md").write_text("hi\n")
        git(repo, "add", "README.md")
        assert git(repo, "commit", "-qm", "docs", check=False).returncode == 0


class TestCLI:
    def test_hook_install_and_uninstall(self, repo, capsys):
        assert main(["hook", "install", "-C", str(repo)]) == 0
        assert main(["hook", "uninstall", "-C", str(repo)]) == 0
        out = capsys.readouterr().out
        assert "installed" in out and "removed" in out

    def test_hook_run_exit_codes(self, repo):
        assert main(["hook", "run", "-C", str(repo)]) == 0
        (repo / ".env").write_text("A=1\n")
        git(repo, "add", ".env")
        assert main(["hook", "run", "-C", str(repo)]) == 1
