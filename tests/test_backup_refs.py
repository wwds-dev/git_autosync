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
        # A local bare repo stands in for the private GitHub one; the real
        # engine refuses a local path unless told this is a test.
        "AUTOSYNC_BACKUP_ALLOW_LOCAL": "1",
        "AUTOSYNC_ALLOWED_EMAILS": "t@example.com 243015673+wwds-dev@users.noreply.github.com",
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


def _ref_key(s: str) -> str:
    """The engine's encoding: every byte outside [A-Za-z0-9_-] as %XX."""
    return "".join(chr(b) if chr(b).isascii() and (chr(b).isalnum() or chr(b) in "_-")
                   else f"%{b:02X}" for b in s.encode())


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

    def test_a_clean_tree_points_its_wip_ref_at_head(self, ws):
        """Not dropped: that is what backs up commits on a detached HEAD."""
        root, repo, backup = ws
        run(root, "proj  push-only\n")
        assert backup_refs(backup)[f"{PREFIX}/wip/main"] == git(repo, "rev-parse", "HEAD")

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
        # Keyed by git's own worktree id, not the folder name, which is not unique.
        wt_id = Path(git(wt, "rev-parse", "--absolute-git-dir")).name
        snap = backup_refs(backup)[f"{PREFIX}/wip/wt/{_ref_key(wt_id)}"]
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
        assert "left out big.bin (over the size limit)" in result.stdout + result.stderr
        assert "BACKUP  proj  (partial:" in result.stdout, "a backup with files left out is not 'ok'"


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

        assert "BLOCKED proj  (secret in work not yet on GitHub" in result.stdout, result.stdout[-1500:]
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


# ----------------------------------------------------------------------
# Review and security findings, 2026-10-20 (docs/audit/full-mac-backup-*.md)
# ----------------------------------------------------------------------
def _commit(repo: Path, name: str, text: str, msg: str, **identity) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    env = {}
    if identity:
        env = {"GIT_AUTHOR_EMAIL": identity["email"], "GIT_COMMITTER_EMAIL": identity["email"]}
    git(repo, "commit", "-qm", msg, env=env)
    return git(repo, "rev-parse", "HEAD")


def _token() -> str:
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    return "ghp_" + "".join(secrets.choice(alphabet) for _ in range(36))


def _stub(root: Path, name: str, body: str) -> str:
    p = root / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(0o755)
    return str(p)


class TestTheScannerFailsClosed:
    def test_an_error_gitleaks_reports_but_exits_0_on_is_a_scanner_error(self, ws):
        """gitleaks 8.30.1 exits 0 with "no leaks found" when its git log fails."""
        root, repo, backup = ws
        (repo / "w.txt").write_text("w\n")
        fake = _stub(root, "gl", 'echo "9:16PM ERR [git] fatal: bad object refs/tags/broken"\n'
                                 'echo "9:16PM INF no leaks found"\nexit 0\n')
        result = run(root, "proj  push-only\n", gitleaks=fake)
        assert "ERROR   proj  (scanner error)" in result.stdout
        assert backup_refs(backup) == {}

    def test_a_ref_git_cannot_read_stops_the_repo(self, ws):
        root, repo, backup = ws
        (repo / ".git" / "refs" / "tags").mkdir(parents=True, exist_ok=True)
        (repo / ".git" / "refs" / "tags" / "broken").write_text("1" * 40 + "\n")
        result = run(root, "proj  push-only\n")
        assert "ERROR   proj  (scanner error)" in result.stdout
        assert backup_refs(backup) == {}


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
class TestTheGateCannotBeTalkedAround:
    def test_a_branch_tip_moved_mid_run_is_still_scanned(self, ws):
        """Plan records the tip, an agent amends the secret away before the
        scan; the recorded commit must not be pushed unscanned."""
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "s")
        _commit(repo, "cfg.txt", f'token = "{_token()}"\n', "oops")
        git(repo, "checkout", "-q", "main")
        # On the first scan, not on `gitleaks version` at start-up: that is
        # after the plan has recorded the tip.
        wrapper = _stub(root, "gl", f'if [ "$1" = git ] && [ ! -f "{root}/moved" ]; then touch "{root}/moved"; '
                                    f'git -C "{repo}" branch -f s main; fi\n'
                                    f'exec "{GITLEAKS}" "$@"\n')
        result = run(root, "proj  push-only\n", gitleaks=wrapper)
        assert "BLOCKED proj" in result.stdout, result.stdout[-1500:]
        assert backup_refs(backup) == {}

    def test_an_untracked_gitleaks_config_does_not_switch_the_gate_off(self, ws):
        root, repo, backup = ws
        (repo / ".gitleaks.toml").write_text("[allowlist]\npaths = ['''.*''']\n")
        (repo / "creds.txt").write_text(f'token = "{_token()}"\n')
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "BLOCKED proj" in result.stdout, result.stdout[-1500:]
        assert backup_refs(backup) == {}

    def test_a_secret_typed_only_in_a_merge_resolution_is_found(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "b")
        _commit(repo, "f.txt", "b\n", "b")
        git(repo, "checkout", "-q", "main")
        _commit(repo, "f.txt", "m\n", "m")
        subprocess.run(["git", "-C", str(repo), "merge", "-q", "b"], capture_output=True)
        (repo / "f.txt").write_text(f'token = "{_token()}"\n')
        git(repo, "add", "f.txt")
        git(repo, "commit", "-qm", "merge b")
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "BLOCKED proj  (secret in history" in result.stdout, result.stdout[-1500:]


class TestWhatIsLeftOut:
    def test_credentials_documents_databases_and_binaries_stay_on_the_mac(self, ws):
        root, repo, backup = ws
        (repo / ".env.local").write_text("DB_PASSWORD=correcthorse42\n")
        (repo / ".netrc").write_text("machine x login y password z\n")
        (repo / "id_service.der").write_bytes(b"0\x82\x01")
        (repo / "app.db").write_bytes(b"SQLite format 3\0")
        (repo / "notes.pdf").write_text("%PDF-1.4 token\n")
        (repo / "blob.bin").write_bytes(b"\0\1\2\3" * 64)
        (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0")
        (repo / "keep.py").write_text("x = 1\n")
        result = run(root, "proj  push-only\n")
        files = set(git(backup, "ls-tree", "-r", "--name-only",
                        backup_refs(backup)[f"{PREFIX}/wip/main"]).splitlines())
        assert {"keep.py", "logo.png"} <= files
        assert not files & {".env.local", ".netrc", "id_service.der", "app.db", "notes.pdf", "blob.bin"}
        assert "BACKUP  proj  (partial:" in result.stdout
        assert "6 file(s) left out" in result.stdout

    def test_big_files_with_odd_names_or_grown_in_place_are_left_out(self, ws):
        root, repo, backup = ws
        (repo / "Übersicht.txt").write_text("x" * 5000)
        (repo / "README.md").write_text("y" * 5000)   # tracked, grown
        run(root, "proj  push-only\n", AUTOSYNC_BACKUP_MAX_BYTES="1024")
        snap = backup_refs(backup)[f"{PREFIX}/wip/main"]
        assert "bersicht" not in git(backup, "ls-tree", "-r", "--name-only", snap)
        assert git(backup, "show", f"{snap}:README.md") == "start", (
            "a grown tracked file must go back to its committed version")

    def test_a_stash_all_does_not_carry_ignored_files(self, ws):
        root, repo, backup = ws
        (repo / ".env").write_text("DB_PASSWORD=x\n")
        (repo / "kept.txt").write_text("k\n")
        git(repo, "stash", "push", "-q", "-a")
        w = git(repo, "rev-parse", "refs/stash")
        run(root, "proj  push-only\n")
        files = git(backup, "ls-tree", "-r", "--name-only", backup_refs(backup)[f"{PREFIX}/stash/{w}"])
        assert "kept.txt" in files and ".env" not in files


class TestWhereItMayPush:
    def test_a_public_backup_repo_is_refused(self, ws):
        root, repo, backup = ws
        gh = _stub(root, "gh", 'echo PUBLIC\n')
        result = run(root, "proj  push-only\n", AUTOSYNC_BACKUP_REMOTE="https://github.com/x/y.git",
                     GH_CMD=gh)
        assert "cannot confirm the backup repo is private" in result.stdout
        assert result.returncode != 0

    def test_a_local_path_is_refused_outside_tests(self, ws):
        root, repo, backup = ws
        result = run(root, "proj  push-only\n", AUTOSYNC_BACKUP_ALLOW_LOCAL="0")
        assert "BACKUP  proj  (failed:" in result.stdout
        assert backup_refs(backup) == {}

    def test_a_url_rewrite_in_the_repo_config_is_refused(self, ws):
        root, repo, backup = ws
        elsewhere = root / "elsewhere.git"
        subprocess.run(["git", "init", "--bare", "-q", str(elsewhere)], check=True)
        git(repo, "config", f"url.{elsewhere}.insteadOf", str(backup))
        result = run(root, "proj  push-only\n")
        assert "redirects the backup URL" in result.stdout
        assert backup_refs(elsewhere) == {}

    def test_a_commit_by_another_identity_is_not_pushed(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "side")
        _commit(repo, "x.py", "x\n", "by someone else", email="real.person@example.org")
        result = run(root, "proj  push-only\n")
        assert "identity that may not be pushed" in result.stdout
        assert backup_refs(backup) == {}

    def test_claudes_noreply_identity_is_allowed_when_listed(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "side")
        _commit(repo, "x.py", "x\n", "by claude", email="noreply@anthropic.com")
        run(root, "proj  push-only\n",
            AUTOSYNC_ALLOWED_EMAILS="t@example.com noreply@anthropic.com")
        assert f"{PREFIX}/heads/side" in backup_refs(backup)


class TestRefNames:
    def test_two_worktrees_with_the_same_folder_name_both_get_backed_up(self, ws):
        root, repo, backup = ws
        for parent in ("a", "b"):
            wt = root / parent / "feat"
            git(repo, "worktree", "add", "-q", "--detach", str(wt))
            (wt / f"{parent}.txt").write_text(parent)
        result = run(root, "proj  push-only\n")
        assert result.returncode == 0, result.stdout[-1500:]
        assert len([r for r in backup_refs(backup) if "/wip/wt/" in r]) == 2

    def test_a_home_relative_entry_can_be_backed_up(self, ws):
        root, repo, backup = ws
        result = run(root, "~/active/proj  push-only\n", HOME=str(root))
        assert "BACKUP  ~/active/proj  (ok:" in result.stdout, result.stdout[-1500:]
        assert any(r.startswith("refs/backup/%7E%2Factive%2Fproj/") for r in backup_refs(backup))

    def test_a_branch_with_a_quote_in_its_name(self, ws):
        root, repo, backup = ws
        git(repo, "branch", "it's-fine")
        run(root, "proj  push-only\n")
        assert f"{PREFIX}/heads/it's-fine" in backup_refs(backup)


class TestWhatTheWipRefMeans:
    def test_commits_on_a_detached_head_are_backed_up(self, ws):
        root, repo, backup = ws
        wt = root / "det"
        git(repo, "worktree", "add", "-q", "--detach", str(wt))
        tip = _commit(wt, "d.py", "d\n", "only on a detached head")
        run(root, "proj  push-only\n")
        assert tip in backup_refs(backup).values()

    def test_an_old_snapshot_is_replaced_once_the_work_is_committed(self, ws):
        """Otherwise restoring wip/main writes older files over committed work."""
        root, repo, backup = ws
        (repo / "README.md").write_text("draft\n")
        run(root, "proj  push-only\n")
        head = _commit(repo, "README.md", "final\n", "final")
        run(root, "proj  push-only\n")
        assert backup_refs(backup)[f"{PREFIX}/wip/main"] == head


class TestReposOriginCannotTake:
    def test_a_listed_repo_without_a_remote_is_still_backed_up(self, ws):
        root, repo, backup = ws
        git(repo, "remote", "remove", "origin")
        result = run(root, "proj  push-only\n")
        assert "SKIP    proj  (no GitHub remote)" in result.stdout
        assert f"{PREFIX}/heads/main" in backup_refs(backup)

    def test_a_repo_mid_conflict_is_backed_up_and_still_an_error(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "b")
        _commit(repo, "f.txt", "b\n", "b")
        git(repo, "checkout", "-q", "main")
        _commit(repo, "f.txt", "m\n", "m")
        subprocess.run(["git", "-C", str(repo), "merge", "-q", "b"], capture_output=True)
        before = fingerprint(repo)
        result = run(root, "proj\n")
        assert "ERROR   proj  (unresolved conflict" in result.stdout
        assert f"{PREFIX}/wip/main" in backup_refs(backup)
        assert fingerprint(repo) == before, "a conflicted tree was staged"

    def test_a_sweep_on_a_published_side_branch_leaves_origin_alone(self, ws):
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "fix/x")
        git(repo, "push", "-q", "-u", "origin", "fix/x")
        _commit(repo, "y.py", "y\n", "local commit")
        on_origin = git(repo, "rev-parse", "origin/fix/x")
        result = run(root, "proj\n")
        assert git(repo, "ls-remote", "origin", "refs/heads/fix/x").split()[0] == on_origin
        assert "backed up only" in result.stdout

    def test_a_fresh_index_lock_is_left_alone(self, ws):
        root, repo, backup = ws
        lock = repo / ".git" / "index.lock"
        lock.write_text("")
        run(root, "proj\n")
        assert lock.exists(), "another git command's live lock was removed"


class TestOtherHardening:
    def test_a_failing_pre_push_hook_does_not_stop_the_backup(self, ws):
        root, repo, backup = ws
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        run(root, "proj  push-only\n")
        assert f"{PREFIX}/heads/main" in backup_refs(backup)

    def test_a_scan_depth_that_is_not_a_number_is_ignored(self, ws):
        root, repo, backup = ws
        marker = root / "pwned"
        result = run(root, "proj  push-only\n",
                     scan_roots=f"{root / 'active'}=a[$(touch {marker})]")
        assert not marker.exists()
        assert "depth is not a number" in result.stdout


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
class TestTheScannerTrustsOnlyWhatIsPublished:
    """Security review, second round: P1-3 had two more routes."""

    def test_a_scanner_config_loosened_in_a_local_commit_is_not_used(self, ws):
        root, repo, backup = ws
        _commit(repo, ".gitleaks.toml", "[allowlist]\npaths = ['''.*''']\n", "tune scanner")
        (repo / "creds.txt").write_text(f'token = "{_token()}"\n')
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "BLOCKED proj" in result.stdout, result.stdout[-1500:]
        assert backup_refs(backup) == {}
        assert "differs from the published one" in result.stdout

    def test_a_commitless_fingerprint_in_the_checkout_does_not_allowlist(self, ws):
        root, repo, backup = ws
        (repo / "creds.txt").write_text(f'token = "{_token()}"\n')
        (repo / ".gitleaksignore").write_text("creds.txt:github-pat:1\n")
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "BLOCKED proj" in result.stdout, result.stdout[-1500:]
        assert backup_refs(backup) == {}

    def test_the_history_scan_ignores_the_checkouts_allowlist_too(self, ws):
        """A committed secret on a branch is the history scan's to catch;
        the scan of unreached commits never sees it."""
        root, repo, backup = ws
        git(repo, "checkout", "-qb", "side")
        _commit(repo, "creds.txt", f'token = "{_token()}"\n', "secret on a branch")
        (repo / ".gitleaksignore").write_text("creds.txt:github-pat:1\n")
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "BLOCKED proj  (secret in history" in result.stdout, result.stdout[-1500:]
        assert backup_refs(backup) == {}

    def test_git_autosyncs_own_allowlist_clears_a_false_positive(self, ws):
        from app.paths import ref_key
        root, repo, backup = ws
        (repo / "creds.txt").write_text(f'token = "{_token()}"\n')
        own = root / "state" / "gitleaksignore"
        own.mkdir(parents=True)
        (own / ref_key("proj")).write_text("creds.txt:github-pat:1\n")
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "BLOCKED" not in result.stdout, result.stdout[-1500:]
        assert f"{PREFIX}/wip/main" in backup_refs(backup)

    def test_the_published_gitleaksignore_still_counts(self, ws):
        root, repo, backup = ws
        _commit(repo, "creds.txt", f'token = "{_token()}"\n', "fixture with a dummy token")
        tip = git(repo, "rev-parse", "HEAD")
        _commit(repo, ".gitleaksignore", f"{tip}:creds.txt:github-pat:1\n", "allowlist the dummy")
        git(repo, "push", "-q", "origin", "main")
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "BLOCKED" not in result.stdout, result.stdout[-1500:]

    def test_scanner_files_inside_dot_git_stop_the_scan(self, ws):
        root, repo, backup = ws
        (repo / ".git" / ".gitleaksignore").write_text("x\n")
        result = run(root, "proj  push-only\n", gitleaks=GITLEAKS)
        assert "ERROR   proj  (scanner error)" in result.stdout
        assert backup_refs(backup) == {}


class TestMoreThatIsLeftOut:
    def test_text_under_a_media_name_lock_files_and_env_variants(self, ws):
        root, repo, backup = ws
        (repo / "fake.png").write_text("token = x\n")
        (repo / "logo.svg").write_text("<svg/>\n")
        (repo / "package-lock.json").write_text("{}\n")
        (repo / ".env-local").write_text("P=x\n")
        (repo / "env.production").write_text("P=x\n")
        (repo / "env.py").write_text("x = 1\n")   # code, not a dotenv file
        run(root, "proj  push-only\n")
        files = set(git(backup, "ls-tree", "-r", "--name-only",
                        backup_refs(backup)[f"{PREFIX}/wip/main"]).splitlines())
        assert "env.py" in files
        assert not files & {"fake.png", "logo.svg", "package-lock.json", ".env-local", "env.production"}
