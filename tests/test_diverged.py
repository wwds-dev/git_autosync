"""`rebase_onto_remote` against real throwaway repos: it either finishes or
leaves the repo exactly as it was."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.diverged import divergence, rebase_onto_remote


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "t@example.com"),
                       ("commit.gpgsign", "false")):
        git(dest, "config", key, value)
    return dest


def _commit(repo: Path, fname: str, text: str, msg: str):
    (repo / fname).write_text(text)
    git(repo, "add", fname)
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def diverged(tmp_path):
    """mine has one local commit, the remote one the clone lacks."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    seed = _clone(remote, tmp_path / "seed")
    _commit(seed, "shared.txt", "base\n", "base")
    git(seed, "push", "-q", "origin", "HEAD:main")
    mine = _clone(remote, tmp_path / "mine")
    theirs = _clone(remote, tmp_path / "theirs")
    git(mine, "config", "pull.rebase", "false")   # prove the code forces rebase
    return mine, theirs


def _snapshot(repo: Path):
    return git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")


def test_clean_rebase_puts_local_commits_on_top(diverged):
    mine, theirs = diverged
    _commit(theirs, "theirs.txt", "t\n", "theirs")
    git(theirs, "push", "-q")
    _commit(mine, "mine.txt", "m\n", "mine")

    result = rebase_onto_remote("git", mine)

    assert result.ok and not result.conflicts and not result.stash_conflict
    assert git(mine, "rev-list", "--count", "HEAD..@{u}") == "0"
    assert git(mine, "rev-list", "--count", "@{u}..HEAD") == "1"
    assert git(mine, "log", "-1", "--format=%s") == "mine"
    # The repo's own config says t@example.com; the rewrite must not use it.
    assert git(mine, "log", "-1", "--format=%cn <%ce>") == (
        "wwds-dev <243015673+wwds-dev@users.noreply.github.com>")


def test_conflict_aborts_and_leaves_repo_untouched(diverged):
    mine, theirs = diverged
    _commit(theirs, "shared.txt", "theirs\n", "theirs")
    git(theirs, "push", "-q")
    _commit(mine, "shared.txt", "mine\n", "mine")
    (mine / "wip.txt").write_text("untracked\n")
    before = _snapshot(mine)

    result = rebase_onto_remote("git", mine)

    assert not result.ok
    assert result.conflicts == ["shared.txt"]
    assert _snapshot(mine) == before
    git_dir = Path(git(mine, "rev-parse", "--absolute-git-dir"))
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "rebase-apply").exists()


def test_uncommitted_changes_survive_a_clean_rebase(diverged):
    mine, theirs = diverged
    _commit(theirs, "theirs.txt", "t\n", "theirs")
    git(theirs, "push", "-q")
    _commit(mine, "mine.txt", "m\n", "mine")
    (mine / "shared.txt").write_text("base\nedited\n")   # tracked, uncommitted

    result = rebase_onto_remote("git", mine)

    assert result.ok and not result.stash_conflict
    assert (mine / "shared.txt").read_text() == "base\nedited\n"
    assert git(mine, "stash", "list") == ""


def test_uncommitted_changes_survive_a_conflicting_rebase(diverged):
    mine, theirs = diverged
    _commit(theirs, "shared.txt", "theirs\n", "theirs")
    git(theirs, "push", "-q")
    _commit(mine, "shared.txt", "mine\n", "mine")
    (mine / "other.txt").write_text("x\n")
    git(mine, "add", "other.txt")
    git(mine, "commit", "-q", "-m", "other")
    (mine / "other.txt").write_text("x\nwip\n")
    before = _snapshot(mine)

    result = rebase_onto_remote("git", mine)

    assert not result.ok
    assert _snapshot(mine) == before
    assert (mine / "other.txt").read_text() == "x\nwip\n"


def test_unreachable_remote_changes_nothing(diverged):
    mine, _ = diverged
    _commit(mine, "mine.txt", "m\n", "mine")
    git(mine, "remote", "set-url", "origin", str(mine.parent / "gone.git"))
    before = _snapshot(mine)

    result = rebase_onto_remote("git", mine)

    assert not result.ok and not result.conflicts
    assert _snapshot(mine) == before


def test_stash_reapply_conflict_is_reported_not_ok_to_push(diverged):
    """git exits 0 here and words it differently per version and locale."""
    mine, theirs = diverged
    _commit(theirs, "shared.txt", "theirs\n", "theirs")
    git(theirs, "push", "-q")
    _commit(mine, "mine.txt", "m\n", "mine")
    (mine / "shared.txt").write_text("my edit\n")   # uncommitted, clashes

    result = rebase_onto_remote("git", mine)

    assert result.ok and result.stash_conflict
    assert result.conflicts == ["shared.txt"]
    assert git(mine, "stash", "list") != ""     # the user's edit is kept


def test_timeout_mid_rebase_is_aborted(diverged, monkeypatch):
    mine, theirs = diverged
    _commit(theirs, "theirs.txt", "t\n", "theirs")
    git(theirs, "push", "-q")
    _commit(mine, "mine.txt", "m\n", "mine")
    before = _snapshot(mine)

    import app.diverged as d
    real = d._git

    def slow_rebase(g, path, *args, timeout=120):
        if "rebase" in args and "--abort" not in args:
            # Leave a real half-done rebase behind, then time out.
            real(g, path, "-c", "sequence.editor=true", "rebase", "-i", "--autostash",
                 "--exec", "false", "@{u}")
            raise subprocess.TimeoutExpired(args, timeout)
        return real(g, path, *args, timeout=timeout)

    monkeypatch.setattr(d, "_git", slow_rebase)
    result = d.rebase_onto_remote("git", mine)

    assert not result.ok and not result.abort_failed
    assert _snapshot(mine) == before


def test_divergence_counts_and_merges(diverged):
    mine, theirs = diverged
    _commit(theirs, "theirs.txt", "t\n", "theirs")
    git(theirs, "push", "-q")
    _commit(mine, "mine.txt", "m\n", "mine")
    git(mine, "fetch", "-q")
    git(mine, "checkout", "-q", "-b", "side", "HEAD~1")
    _commit(mine, "side.txt", "s\n", "side")
    git(mine, "checkout", "-q", "main")
    git(mine, "merge", "-q", "--no-ff", "-m", "merge side", "side")

    div = divergence("git", mine)

    assert (div.ahead, div.behind, div.merges) == (3, 1, 1)


def test_divergence_none_without_branch_or_upstream(diverged):
    mine, _ = diverged
    git(mine, "checkout", "-q", "--detach")
    assert divergence("git", mine) is None
    git(mine, "checkout", "-q", "-b", "local-only")
    assert divergence("git", mine) is None


def _unlinked(tmp_path, local_behind=0, local_ahead=0):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    seed = _clone(remote, tmp_path / "seed")
    _commit(seed, "a.txt", "a\n", "a")
    git(seed, "push", "-q", "origin", "HEAD:main")
    mine = _clone(remote, tmp_path / "mine")
    for i in range(local_ahead):
        _commit(mine, f"m{i}.txt", "m\n", f"m{i}")
    for i in range(local_behind):
        _commit(seed, f"s{i}.txt", "s\n", f"s{i}")
    if local_behind:
        git(seed, "push", "-q", "origin", "HEAD:main")
    git(mine, "branch", "--unset-upstream")
    return mine


def test_link_sets_upstream_without_touching_anything(tmp_path):
    from app.diverged import link_to_origin
    mine = _unlinked(tmp_path)
    before = _snapshot(mine)
    res = link_to_origin("git", mine)
    assert res.ok and (res.ahead, res.behind) == (0, 0)
    assert git(mine, "rev-parse", "--abbrev-ref", "@{u}") == "origin/main"
    assert _snapshot(mine) == before


def test_link_reports_ahead_and_behind_and_changes_no_commits(tmp_path):
    from app.diverged import link_to_origin
    mine = _unlinked(tmp_path / "x", local_behind=2, local_ahead=1)
    head = git(mine, "rev-parse", "HEAD")
    res = link_to_origin("git", mine)
    assert res.ok and (res.ahead, res.behind) == (1, 2)
    assert git(mine, "rev-parse", "HEAD") == head


def test_link_refuses_a_branch_github_does_not_have(tmp_path):
    from app.diverged import link_to_origin
    mine = _unlinked(tmp_path)
    git(mine, "checkout", "-q", "-b", "brand-new")
    res = link_to_origin("git", mine)
    assert not res.ok and "no branch" in res.message
    assert subprocess.run(["git", "-C", str(mine), "rev-parse", "@{u}"],
                          capture_output=True).returncode != 0


def test_link_refuses_detached_head(tmp_path):
    from app.diverged import link_to_origin
    mine = _unlinked(tmp_path)
    git(mine, "checkout", "-q", "--detach")
    assert not link_to_origin("git", mine).ok
