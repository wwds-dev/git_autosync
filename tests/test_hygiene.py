"""scripts/hygiene.py against real throwaway repos: each way work can be left
behind (uncommitted, unpushed, behind, unmerged branch, dirty worktree) is
reported, and a clean repo is not."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "hygiene.py"
spec = importlib.util.spec_from_file_location("hygiene", SCRIPT)
hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hygiene)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, name: str, text: str = "x\n") -> None:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path):
    """A clone of a bare remote, one commit pushed, in step with origin."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(work, "config", k, v)
    git(work, "checkout", "-q", "-b", "main")
    commit(work, "a.txt")
    git(work, "push", "-q", "-u", "origin", "main")
    git(work, "remote", "set-head", "origin", "main")
    return work


def run(repo: Path, fetch: bool = False) -> dict:
    return hygiene.inspect("r", repo, fetch)


def test_clean_repo_has_no_problems(repo):
    row = run(repo)
    assert row["problems"] == []
    assert (row["dirty"], row["ahead"], row["behind"], row["unmerged_count"]) == (0, 0, 0, 0)


def test_uncommitted_file(repo):
    (repo / "new.txt").write_text("hi")
    (repo / "a.txt").write_text("changed")
    row = run(repo)
    assert row["dirty"] == 2
    assert "2 uncommitted files" in row["problems"]


def test_unpushed_commit(repo):
    commit(repo, "b.txt")
    row = run(repo)
    assert row["ahead"] == 1
    assert "1 unpushed commit" in row["problems"]


def test_behind_after_fetch_only(repo, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "remote.git"), str(other)], check=True)
    for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(other, "config", k, v)
    commit(other, "c.txt")
    git(other, "push", "-q", "origin", "main")
    assert run(repo, fetch=False)["behind"] == 0       # not fetched: cannot know
    row = run(repo, fetch=True)
    assert row["behind"] == 1 and row["fetched"] is True
    assert any("behind" in p for p in row["problems"])


def test_diverged(repo, tmp_path):
    commit(repo, "mine.txt")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "remote.git"), str(other)], check=True)
    for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(other, "config", k, v)
    commit(other, "theirs.txt")
    git(other, "push", "-q", "origin", "main")
    row = run(repo, fetch=True)
    assert (row["ahead"], row["behind"]) == (1, 1)
    assert row["problems"] == ["diverged: 1 unpushed, 1 behind"]


def test_unmerged_branch_listed_and_merged_branch_not(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "f.txt")
    git(repo, "checkout", "-q", "-b", "done")           # at main's tip: merged
    git(repo, "checkout", "-q", "main")
    git(repo, "branch", "-f", "done", "main")
    row = run(repo)
    assert row["unmerged"] == ["feature"]
    assert "1 branch not merged into main" in row["problems"]


def test_branch_without_remote_counts_as_unpushed(repo):
    git(repo, "checkout", "-q", "-b", "local-only")
    commit(repo, "l.txt")
    row = run(repo)
    assert row["upstream"] is None and row["ahead"] == 1
    assert any("no remote branch" in n for n in row["notes"])


def test_dirty_linked_worktree(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(wt), "main")
    (wt / "wip.txt").write_text("unfinished")
    row = run(repo)
    assert row["worktrees"][0]["dirty"] == 1
    assert "1 worktree with uncommitted work" in row["problems"]


def test_not_a_repo_is_an_error_row(tmp_path):
    row = hygiene.inspect("x", tmp_path, False)
    assert row["error"] == "not a git repository"


def test_repo_list_resolves_names_and_skips_comments(tmp_path):
    lab = tmp_path / "lab"
    (lab / "active" / "one").mkdir(parents=True)
    listing = tmp_path / "repos.txt"
    listing.write_text("# c\n\none  push-only\none\n/abs/elsewhere\n")
    found = hygiene.read_repo_list(listing, lab)
    assert [n for n, _ in found] == ["one", "/abs/elsewhere"]   # duplicate dropped
    assert found[0][1] == (lab / "active" / "one").resolve()


def test_report_counts_attention_and_writes_atomically(repo, tmp_path):
    (repo / "dirty.txt").write_text("x")
    out = tmp_path / "state" / "hygiene.json"
    rc = hygiene.main(["--repos", str(_listing(tmp_path, repo)), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["attention"] == 1 and data["repos"][0]["dirty"] == 1
    assert not [p for p in out.parent.iterdir() if p.name.startswith(".hygiene-")]


def _listing(tmp_path: Path, repo: Path) -> Path:
    f = tmp_path / "list.txt"
    f.write_text(f"{repo}\n")
    return f
