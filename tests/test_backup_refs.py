"""Backups of everything `origin` does not have, end to end against real git.

docs/specs/full-mac-backup.md: every local branch, every worktree's
uncommitted work and every stash goes to hidden refs in a separate backup
repo, without touching the repo it came from. These drive the real engine
against throwaway repos, a throwaway origin and a throwaway backup repo.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent / "git_autosync.sh"
GITLEAKS = shutil.which("gitleaks")


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True,
                          env={**os.environ, **(env or {})}).stdout.strip()


def _init(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "t@example.com"),
                       ("commit.gpgsign", "false")):
        git(path, "config", key, value)
    return path


@pytest.fixture
def ws(tmp_path):
    """origin + backup (both bare) and a clone at active/proj."""
    origin = tmp_path / "origin.git"
    backup = tmp_path / "backup.git"
    for bare in (origin, backup):
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    repo = _init(tmp_path / "active" / "proj")
    (repo / ".gitignore").write_text(".env\n")
    (repo / "README.md").write_text("start\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    return tmp_path, repo, backup


def run(root: Path, config_text: str, *extra: str, gitleaks: str = "/usr/bin/true",
        scan_roots: str = "", **env_extra):
    config = root / "repos.txt"
    config.write_text(config_text)
    env = {
        **os.environ,
        "AUTOSYNC_CONFIG": str(config),
        "AUTOSYNC_LOG_DIR": str(root / "logs"),
        "AUTOSYNC_STATE_DIR": str(root / "state"),
        "LAB_ACTIVE": str(root / "active"),
        "GITLEAKS_CMD": gitleaks,
        "AUTOSYNC_BACKUP_REMOTE": str(root / "backup.git"),
        "AUTOSYNC_SCAN_ROOTS": scan_roots,
        **env_extra,
    }
    result = subprocess.run(["bash", str(ENGINE), *extra], capture_output=True,
                            text=True, env=env)
    assert "not a git repo" not in result.stdout, result.stdout[-800:]
    return result


def backup_refs(backup: Path) -> dict[str, str]:
    out = git(backup, "for-each-ref", "--format=%(objectname) %(refname)")
    return {ref: sha for sha, ref in (line.split() for line in out.splitlines())}


def fingerprint(repo: Path) -> str:
    """Everything the backup must leave alone: files, index, HEAD, refs, stash."""
    h = hashlib.sha256()
    for p in sorted(repo.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(repo).parts[:1]:
            h.update(str(p.relative_to(repo)).encode() + p.read_bytes())
    gd = repo / ".git"
    for name in ("index", "HEAD"):
        f = gd / name
        h.update(f.read_bytes() if f.exists() else b"-")
    h.update(git(repo, "for-each-ref", "--format=%(objectname) %(refname)",
                 "refs/heads", "refs/stash").encode())
    h.update(git(repo, "stash", "list").encode())
    return h.hexdigest()


PREFIX = "refs/backup/proj"


class TestBranches:
    def test_a_local_only_side_branch_reaches_the_backup_not_origin(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "fix/thing")
        (repo / "a.py").write_text("x = 1\n")
        git(repo, "add", "a.py")
        git(repo, "commit", "-qm", "local only")
        tip = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "main")
        origin_before = git(repo, "ls-remote", "origin", "refs/heads/*")

        result = run(root, "proj  push-only\n")

        assert result.returncode == 0, result.stdout[-1500:]
        assert backup_refs(backup)[f"{PREFIX}/heads/fix/thing"] == tip
        assert git(repo, "ls-remote", "origin", "refs/heads/*") == origin_before
        assert "BACKUP  proj  (ok:" in result.stdout

    def test_branches_named_wip_do_not_collide_with_snapshots(self, ws):
        """The brief's layout put a branch `wip` at refs/backup/wip and main's
        snapshot at refs/backup/wip/main; git refuses a ref that is also a
        directory, so one of the two could never be stored."""
        root, repo, backup = ws
        git(repo, "branch", "wip")
        (repo / "README.md").write_text("edited\n")

        result = run(root, "proj  push-only\n")

        refs = backup_refs(backup)
        assert result.returncode == 0, result.stdout[-1500:]
        assert {f"{PREFIX}/heads/wip", f"{PREFIX}/wip/main"} <= set(refs)

    def test_a_rebased_branch_overwrites_its_backup(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "topic")
        (repo / "t.py").write_text("1\n"); git(repo, "add", "t.py"); git(repo, "commit", "-qm", "one")
        run(root, "proj  push-only\n")
        git(repo, "commit", "-q", "--amend", "-m", "one, reworded")
        run(root, "proj  push-only\n")
        assert backup_refs(backup)[f"{PREFIX}/heads/topic"] == git(repo, "rev-parse", "HEAD")

    def test_a_subpath_entry_is_one_ref_component(self, ws):
        root, repo, backup = ws
        nested = _init(root / "active" / "group" / "sub")
        (nested / "f").write_text("f\n"); git(nested, "add", "f"); git(nested, "commit", "-qm", "f")
        git(nested, "remote", "add", "origin", str(root / "origin.git"))

        run(root, "group/sub  push-only\n")

        assert "refs/backup/group%2Fsub/heads/main" in backup_refs(backup)


class TestUncommittedWork:
    def test_tracked_and_untracked_edits_are_snapshotted_and_nothing_moves(self, ws):
        root, repo, backup = ws
        (repo / "README.md").write_text("edited, not committed\n")
        (repo / "new_untracked.py").write_text("print('new')\n")
        (repo / "staged.py").write_text("staged\n")
        git(repo, "add", "staged.py")
        (repo / ".env").write_text("IGNORED=1\n")
        before = fingerprint(repo)

        result = run(root, "proj  push-only\n")

        assert result.returncode == 0, result.stdout[-1500:]
        assert fingerprint(repo) == before, "the backup touched the repo"
        snap = backup_refs(backup)[f"{PREFIX}/wip/main"]
        files = git(backup, "ls-tree", "-r", "--name-only", snap).splitlines()
        assert {"README.md", "new_untracked.py", "staged.py"} <= set(files)
        assert ".env" not in files, "an ignored file was backed up"
        assert git(backup, "show", f"{snap}:README.md") == "edited, not committed"
        assert git(backup, "rev-parse", f"{snap}^") == git(repo, "rev-parse", "HEAD")

    def test_a_clean_tree_has_no_snapshot(self, ws):
        root, repo, backup = ws
        run(root, "proj  push-only\n")
        assert f"{PREFIX}/wip/main" not in backup_refs(backup)

    def test_an_unchanged_tree_is_not_pushed_again(self, ws):
        """Snapshot SHAs are deterministic, or every night re-pushes everything."""
        root, repo, backup = ws
        (repo / "wip.txt").write_text("w\n")
        run(root, "proj  push-only\n")
        first = backup_refs(backup)[f"{PREFIX}/wip/main"]

        result = run(root, "proj  push-only\n")

        assert backup_refs(backup)[f"{PREFIX}/wip/main"] == first
        assert "backup: up to date" in result.stdout

    def test_a_linked_worktree_on_a_detached_head_is_snapshotted(self, ws):
        root, repo, backup = ws
        wt = root / "elsewhere" / "my wt"
        git(repo, "worktree", "add", "-q", "--detach", str(wt))
        (wt / "in_worktree.txt").write_text("only here\n")

        result = run(root, "proj  push-only\n")

        assert result.returncode == 0, result.stdout[-1500:]
        snap = backup_refs(backup)[f"{PREFIX}/wip/wt/my_wt"]
        assert "in_worktree.txt" in git(backup, "ls-tree", "-r", "--name-only", snap)

    def test_a_stash_with_untracked_files_is_flattened(self, ws):
        root, repo, backup = ws
        (repo / "README.md").write_text("stashed edit\n")
        (repo / "stashed_new.txt").write_text("u\n")
        git(repo, "stash", "push", "-q", "-u", "-m", "parked")
        w = git(repo, "rev-parse", "refs/stash")

        run(root, "proj  push-only\n")

        snap = backup_refs(backup)[f"{PREFIX}/stash/{w}"]
        assert git(backup, "show", f"{snap}:README.md") == "stashed edit"
        assert git(backup, "show", f"{snap}:stashed_new.txt") == "u"
        assert len(git(backup, "rev-list", "--parents", "-n1", snap).split()) == 2, (
            "a stash backup must be an ordinary commit, or the scanner sees no diff")

    def test_a_big_new_file_is_left_out_and_reported(self, ws):
        root, repo, backup = ws
        (repo / "big.bin").write_bytes(b"\0" * 4096)
        (repo / "small.txt").write_text("s\n")

        result = run(root, "proj  push-only\n", AUTOSYNC_BACKUP_MAX_BYTES="1024")

        files = git(backup, "ls-tree", "-r", "--name-only", backup_refs(backup)[f"{PREFIX}/wip/main"])
        assert "small.txt" in files and "big.bin" not in files
        assert "1 big file(s) left out" in result.stdout


class TestDryRun:
    def test_reports_missing_and_pushes_nothing(self, ws):
        root, repo, backup = ws
        (repo / "w.txt").write_text("w\n")
        result = run(root, "proj  push-only\n", "--dry-run")
        assert backup_refs(backup) == {}
        assert "BACKUP  proj  (missing:" in result.stdout

    def test_a_push_only_dry_run_keeps_what_the_user_staged(self, ws):
        """Regression: the engine reset the index after every dry-run, which in
        push-only mode unstaged the user's own work."""
        root, repo, backup = ws
        (repo / "staged.py").write_text("s\n")
        git(repo, "add", "staged.py")
        run(root, "proj  push-only\n", "--dry-run")
        assert "A  staged.py" in git(repo, "status", "--short")


class TestSweepOnASideBranch:
    def test_commits_nothing_and_publishes_no_branch(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "fix/side")
        (repo / "work.py").write_text("half done\n")
        head = git(repo, "rev-parse", "HEAD")

        result = run(root, "proj\n")

        assert git(repo, "rev-parse", "HEAD") == head, "sweep committed on a side branch"
        assert "fix/side" not in git(repo, "ls-remote", "--heads", "origin")
        assert f"{PREFIX}/wip/main" in backup_refs(backup)
        assert "not the default branch" in result.stdout

    def test_still_commits_on_the_default_branch(self, ws):
        root, repo, backup = ws
        (repo / "work.py").write_text("done\n")
        head = git(repo, "rev-parse", "HEAD")
        run(root, "proj\n")
        assert git(repo, "rev-parse", "HEAD") != head


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
class TestTheGateCoversSnapshots:
    def test_a_secret_in_an_untracked_file_blocks_the_backup(self, ws):
        root, repo, backup = ws
        token = "ghp_" + "Zq7Xw2Lp9Rt4Vb8Nm3Kc6Hd1Jf5Gs0Ay2Ue"
        (repo / "notes.txt").write_text(f'token = "{token}"\n')

        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)

        assert "BLOCKED proj  (secret in uncommitted work" in result.stdout, result.stdout[-1500:]
        assert backup_refs(backup) == {}
        assert result.returncode != 0


class TestRestore:
    def test_a_fresh_clone_gets_every_branch_and_snapshot_back(self, ws):
        """The success measure: lose the Mac, restore from the two URLs."""
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "local/only")
        (repo / "c.py").write_text("c\n"); git(repo, "add", "c.py"); git(repo, "commit", "-qm", "c")
        (repo / "wip.py").write_text("unsaved\n")
        run(root, "proj  push-only\n")

        fresh = root / "restored"
        subprocess.run(["git", "clone", "-q", str(root / "origin.git"), str(fresh)], check=True)
        git(fresh, "fetch", "-q", str(backup), f"+{PREFIX}/*:refs/backup/*")
        assert git(fresh, "rev-parse", "refs/backup/heads/local/only") == git(repo, "rev-parse", "local/only")
        assert git(fresh, "show", "refs/backup/wip/main:wip.py") == "unsaved"


class TestFailures:
    def test_an_unreachable_backup_repo_is_an_error_not_a_pass(self, ws):
        root, repo, backup = ws
        result = run(root, "proj  push-only\n", AUTOSYNC_BACKUP_REMOTE=str(root / "nope.git"))
        assert "BACKUP  proj  (failed:" in result.stdout
        assert result.returncode != 0
        assert (root / "state" / "last_status.txt").read_text().strip() == "attention"

    def test_empty_remote_switches_backups_off(self, ws):
        root, repo, backup = ws
        (repo / "w.txt").write_text("w\n")
        result = run(root, "proj  push-only\n", AUTOSYNC_BACKUP_REMOTE="")
        assert "BACKUP" not in result.stdout
        assert result.returncode == 0


class TestUnlistedRepos:
    def test_a_repo_missing_from_the_list_is_flagged(self, ws):
        root, repo, backup = ws
        _init(root / "active" / "forgotten")
        websites = root / "Websites"
        _init(websites / "site")
        wt = root / "active" / "proj-wt"
        git(repo, "worktree", "add", "-q", "--detach", str(wt))

        result = run(root, "proj  push-only\n",
                     scan_roots=f"{root / 'active'}=4:{websites}=2")

        out = result.stdout
        assert "UNLISTED" in out and "forgotten" in out and "site" in out
        assert "no GitHub remote" in out
        assert "proj-wt" not in out, "a linked worktree is part of its repo"
        assert "UNLISTED: " + str(repo) not in out
        assert "unlisted=2" in out
        assert (root / "state" / "last_status.txt").read_text().strip() == "attention"

    def test_a_single_repo_run_does_not_scan(self, ws):
        root, repo, backup = ws
        _init(root / "active" / "forgotten")
        result = run(root, "proj  push-only\n", "--repo", "proj",
                     scan_roots=f"{root / 'active'}=4")
        assert "UNLISTED" not in result.stdout
