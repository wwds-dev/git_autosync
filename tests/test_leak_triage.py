"""Blocked-repo triage, exercised against real repos with a throwaway remote.

The purge these guard used to rewrite nothing: the engine redacts gitleaks
output, so the "secret" it replaced was the word REDACTED — then it
force-pushed and reported the history clean. These tests use a real gitleaks
and a real git-filter-repo where the behaviour depends on them, and skip
(rather than pass) when either is missing.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import leak_triage as lt

GITLEAKS = shutil.which("gitleaks") or (
    "/opt/homebrew/bin/gitleaks" if Path("/opt/homebrew/bin/gitleaks").exists() else None)
FILTER_REPO = shutil.which("git-filter-repo") or (
    "/opt/homebrew/bin/git-filter-repo"
    if Path("/opt/homebrew/bin/git-filter-repo").exists() else None)
needs_gitleaks = pytest.mark.skipif(not GITLEAKS, reason="gitleaks not installed")
needs_filter_repo = pytest.mark.skipif(not (GITLEAKS and FILTER_REPO),
                                       reason="gitleaks/git-filter-repo not installed")

# Shaped like a GitHub token so gitleaks' github-pat rule fires on it.
FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def workspace(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "t@example.com"),
                       ("commit.gpgsign", "false")):
        git(repo, "config", key, value)
    (repo / "README.md").write_text("start\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo, remote


def commit_token(repo: Path) -> str:
    (repo / "settings.py").write_text(f'GITHUB_TOKEN = "{FAKE_TOKEN}"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add settings")
    return git(repo, "rev-parse", "HEAD")


def finding_for(repo: Path, commit: str) -> dict:
    """The finding as the GUI holds it: fingerprint from a redacted scan."""
    out = subprocess.run([GITLEAKS, "git", "--no-banner", "--redact", "--verbose",
                          "--log-opts", f"-1 {commit}", str(repo)],
                         capture_output=True, text=True).stdout
    fp = next(line.split(":", 1)[1].strip() for line in out.splitlines()
              if line.startswith("Fingerprint:"))
    return {"file": "settings.py", "rule": "github-pat", "line": "1",
            "fingerprint": fp, "secret": "REDACTED"}


# ── .gitleaksignore edits ─────────────────────────────────────────

class TestIgnoreFile:
    def test_add_and_remove_keep_comments(self, tmp_path):
        path = tmp_path / ".gitleaksignore"
        path.write_text("# why this is allowlisted\nabc:f.py:rule:1\n")
        assert lt.append_ignore_entry(path, "def:g.py:rule:2")
        assert not lt.append_ignore_entry(path, "def:g.py:rule:2"), "duplicate added"
        assert lt.remove_ignore_entry(path, "def:g.py:rule:2")
        assert path.read_text() == "# why this is allowlisted\nabc:f.py:rule:1\n"

    def test_entries_skip_comments_and_blanks(self, tmp_path):
        path = tmp_path / ".gitleaksignore"
        path.write_text("# note\n\nabc:f.py:rule:1\n")
        assert lt.ignore_entries(path) == ["abc:f.py:rule:1"]

    def test_append_to_file_without_trailing_newline(self, tmp_path):
        path = tmp_path / ".gitleaksignore"
        path.write_text("abc:f.py:rule:1")
        lt.append_ignore_entry(path, "def:g.py:rule:2")
        assert path.read_text() == "abc:f.py:rule:1\ndef:g.py:rule:2\n"

    def test_remove_missing_is_false(self, tmp_path):
        assert not lt.remove_ignore_entry(tmp_path / ".gitleaksignore", "x")


# ── Where the finding lives ───────────────────────────────────────

class TestLocate:
    def test_staged_finding_has_no_commit(self, workspace):
        repo, _ = workspace
        finding = {"fingerprint": "settings.py:github-pat:1"}
        assert lt.finding_commit(finding) is None
        assert lt.locate(repo, finding) == lt.UNCOMMITTED

    def test_local_commit_is_not_published(self, workspace):
        repo, _ = workspace
        sha = commit_token(repo)
        assert lt.locate(repo, {"fingerprint": f"{sha}:settings.py:github-pat:1"}) == lt.LOCAL

    def test_pushed_commit_is_published(self, workspace):
        repo, _ = workspace
        sha = commit_token(repo)
        git(repo, "push", "-q", "origin", "main")
        assert lt.locate(repo, {"fingerprint": f"{sha}:settings.py:github-pat:1"}) == lt.PUBLISHED

    def test_unknown_commit_counts_as_published(self, workspace):
        repo, _ = workspace
        assert lt.is_published(repo, "0" * 40)


# ── Resolving the real value ──────────────────────────────────────

@needs_gitleaks
class TestResolveSecret:
    def test_returns_the_real_value_not_redacted(self, workspace):
        repo, _ = workspace
        finding = finding_for(repo, commit_token(repo))
        assert lt.resolve_secret(repo, finding, GITLEAKS) == FAKE_TOKEN

    def test_mismatched_fingerprint_returns_none(self, workspace):
        repo, _ = workspace
        finding = finding_for(repo, commit_token(repo))
        finding["fingerprint"] = finding["fingerprint"].replace(":1", ":9")
        assert lt.resolve_secret(repo, finding, GITLEAKS) is None

    def test_staged_finding_returns_none(self, workspace):
        repo, _ = workspace
        assert lt.resolve_secret(repo, {"fingerprint": "settings.py:github-pat:1"},
                                 GITLEAKS) is None


# ── The purge ─────────────────────────────────────────────────────

@needs_filter_repo
class TestPurge:
    def test_published_secret_is_removed_and_force_pushed(self, workspace):
        repo, remote = workspace
        sha = commit_token(repo)
        git(repo, "push", "-q", "origin", "main")
        secret = lt.resolve_secret(repo, finding_for(repo, sha), GITLEAKS)
        assert lt.published_with(repo, secret)

        lt.purge_secret(repo, secret, FILTER_REPO, GITLEAKS, push=True)

        assert lt.commits_containing(repo, secret) == []
        assert FAKE_TOKEN not in git(remote, "show", "main:settings.py")
        assert git(repo, "remote", "get-url", "origin") == str(remote)
        assert git(repo, "rev-parse", "--abbrev-ref", "main@{u}") == "origin/main"

    def test_local_only_purge_does_not_push(self, workspace):
        repo, remote = workspace
        before = git(remote, "rev-parse", "main")
        sha = commit_token(repo)
        secret = lt.resolve_secret(repo, finding_for(repo, sha), GITLEAKS)
        assert not lt.published_with(repo, secret)

        lt.purge_secret(repo, secret, FILTER_REPO, GITLEAKS, push=False)

        assert lt.commits_containing(repo, secret) == []
        assert git(remote, "rev-parse", "main") == before, "local-only purge pushed"

    def test_dirty_tree_refuses(self, workspace):
        repo, remote = workspace
        sha = commit_token(repo)
        (repo / "wip.txt").write_text("unsaved work\n")
        with pytest.raises(lt.PurgeError, match="uncommitted"):
            lt.purge_secret(repo, FAKE_TOKEN, FILTER_REPO, GITLEAKS)
        assert git(repo, "rev-parse", "HEAD") == sha

    def test_redacted_placeholder_cannot_force_push(self, workspace):
        """The old bug: 'REDACTED' was purged, force-pushed, and called clean.

        A rewrite on the wrong value leaves the secret in place, so the scan
        before the push has to catch it and refuse.
        """
        repo, remote = workspace
        commit_token(repo)
        git(repo, "push", "-q", "origin", "main")
        before = git(remote, "rev-parse", "main")
        with pytest.raises(lt.PurgeError, match="nothing was pushed"):
            lt.purge_secret(repo, "REDACTED", FILTER_REPO, GITLEAKS, push=True)
        assert git(remote, "rev-parse", "main") == before

    def test_other_leak_blocks_the_push(self, workspace):
        repo, remote = workspace
        sha = commit_token(repo)
        (repo / "other.py").write_text(
            'AWS = "AKIA' + "QYLPMN5HHHFPZAM2" + '"\n')
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "another")
        git(repo, "push", "-q", "origin", "main")
        before = git(remote, "rev-parse", "main")
        secret = lt.resolve_secret(repo, finding_for(repo, sha), GITLEAKS)
        with pytest.raises(lt.PurgeError, match="other"):
            lt.purge_secret(repo, secret, FILTER_REPO, GITLEAKS, push=True)
        assert git(remote, "rev-parse", "main") == before
