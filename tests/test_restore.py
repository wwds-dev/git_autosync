"""restore.sh, end to end: back up a Mac, restore it into an empty home, compare.

docs/specs/restore-and-continue.md, success measure "Restore drill". The engine
runs with HOME pointing at one scratch home; restore.sh then runs with HOME at a
second, empty one, so the manifest's ~/ paths land there.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "git_autosync.sh"
RESTORE = ROOT / "restore.sh"
EMAIL = "t@example.com"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init(path: Path, origin: Path | None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for k, v in (("user.name", "T"), ("user.email", EMAIL), ("commit.gpgsign", "false")):
        git(path, "config", k, v)
    (path / "README.md").write_text("start\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "initial")
    if origin is not None:
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
        subprocess.run(["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        git(path, "remote", "add", "origin", str(origin))
        git(path, "push", "-q", "-u", "origin", "main")
    return path


def _env(root: Path, home: Path, **extra) -> dict:
    return {**os.environ, "HOME": str(home), "LAB_ACTIVE": str(home / "active"),
            "AUTOSYNC_CONFIG": str(root / "repos.txt"),
            "AUTOSYNC_LOG_DIR": str(root / "logs"), "AUTOSYNC_STATE_DIR": str(root / "state"),
            "AUTOSYNC_IGNORE_DIR": str(root / "ignore"),
            "AUTOSYNC_BACKUP_REMOTE": str(root / "backup.git"), "AUTOSYNC_BACKUP_ALLOW_LOCAL": "1",
            "AUTOSYNC_ALLOWED_EMAILS": f"{EMAIL} 243015673+wwds-dev@users.noreply.github.com",
            "AUTOSYNC_SCAN_ROOTS": "", "GITLEAKS_CMD": "/usr/bin/true",
            "GIT_CONFIG_GLOBAL": "/dev/null", **extra}


@pytest.fixture
def mac(tmp_path):
    """A Mac worth losing: side branches, uncommitted work, a worktree, a
    stash, a nested repo and one with no origin."""
    home = tmp_path / "home1"
    subprocess.run(["git", "init", "--bare", "-q", str(tmp_path / "backup.git")], check=True)
    proj = _init(home / "active" / "proj", tmp_path / "proj.git")
    git(proj, "checkout", "-qb", "feature/local-only")
    (proj / "f.py").write_text("feature\n")
    git(proj, "add", "f.py")
    git(proj, "commit", "-qm", "only on this Mac")
    (proj / "stashed.txt").write_text("s\n")
    git(proj, "stash", "push", "-q", "-u", "-m", "parked")
    (proj / "README.md").write_text("edited, not committed\n")
    (proj / "new_untracked.txt").write_text("untracked\n")
    wt = home / "elsewhere" / "wt1"
    git(proj, "worktree", "add", "-q", "--detach", str(wt), "main")
    (wt / "in_worktree.txt").write_text("worktree work\n")
    nested = _init(home / "active" / "proj" / "sub", tmp_path / "sub.git")
    (proj / ".gitignore").write_text("sub/\n")
    _init(home / "active" / "local", None)   # no origin at all
    (tmp_path / "repos.txt").write_text("proj  push-only\nproj/sub\nlocal  push-only\n")
    r = subprocess.run(["bash", str(ENGINE)], capture_output=True, text=True,
                       env=_env(tmp_path, home))
    assert "MANIFEST" in r.stdout and "manifest: pushed" in r.stdout, r.stdout[-2000:]
    return tmp_path, home, proj, nested


def _restore(root: Path, home: Path, *args: str):
    return subprocess.run(["bash", str(RESTORE), *args], capture_output=True, text=True,
                          env=_env(root, home, AUTOSYNC_SUPPORT_DIR=str(home / "support")))


def test_a_lost_mac_comes_back_in_an_empty_home(mac):
    root, home, proj, nested = mac
    new_home = root / "home2"
    r = _restore(root, new_home)
    assert r.returncode == 0, r.stdout + r.stderr
    p = new_home / "active" / "proj"
    # every branch at its backed-up commit
    assert git(p, "rev-parse", "feature/local-only") == git(proj, "rev-parse", "feature/local-only")
    assert git(p, "rev-parse", "main") == git(proj, "rev-parse", "main")
    # on the branch it was on, with the edits back as uncommitted changes
    assert git(p, "branch", "--show-current") == "feature/local-only"
    assert (p / "README.md").read_text() == "edited, not committed\n"
    assert (p / "new_untracked.txt").read_text() == "untracked\n"
    status = git(p, "status", "--porcelain")
    assert "M README.md" in status and "?? new_untracked.txt" in status, status
    # the worktree's work and the stash, as branches
    branches = git(p, "branch", "--format=%(refname:short)").splitlines()
    wt_branch = next(b for b in branches if b.startswith("restored/wt/"))
    assert git(p, "show", f"{wt_branch}:in_worktree.txt") == "worktree work"
    st_branch = next(b for b in branches if b.startswith("restored/stash/"))
    assert git(p, "show", f"{st_branch}:stashed.txt") == "s"
    # nothing of the backup's layout is left behind
    assert git(p, "for-each-ref", "refs/restore/") == ""
    # the nested repo inside its parent, and the one with no origin
    assert git(new_home / "active" / "proj" / "sub", "rev-parse", "HEAD") == git(nested, "rev-parse", "HEAD")
    assert (new_home / "active" / "local" / "README.md").exists()
    # git_autosync's own state
    assert (new_home / "support" / "autosync_repos.txt").read_text() == (root / "repos.txt").read_text()
    assert "restored=3 skipped=0 failed=0" in r.stdout


def test_it_never_writes_into_a_folder_that_has_files(mac):
    root, home, proj, nested = mac
    new_home = root / "home2"
    keep = new_home / "active" / "proj" / "mine.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("do not touch\n")
    r = _restore(root, new_home)
    assert "SKIP  proj" in r.stdout
    assert keep.read_text() == "do not touch\n"
    assert not (keep.parent / ".git").exists()


def test_a_dry_run_changes_nothing(mac):
    root, home, proj, nested = mac
    new_home = root / "home2"
    r = _restore(root, new_home, "--dry-run")
    assert r.returncode == 0 and "WOULD proj" in r.stdout
    assert not new_home.exists()


def test_the_manifest_carries_no_credentials_from_a_remote_url(mac):
    root, home, proj, nested = mac
    git(proj, "remote", "set-url", "origin", "https://user:ghp_abc123@github.com/x/proj.git")
    r = subprocess.run(["bash", str(ENGINE), "--backup-only"], capture_output=True, text=True,
                       env=_env(root, home))
    assert r.returncode in (0, 1)
    tsv = git(root / "backup.git", "show", "refs/backup-meta/manifest:manifest.tsv")
    assert "ghp_abc123" not in tsv and "https://github.com/x/proj.git" in tsv
