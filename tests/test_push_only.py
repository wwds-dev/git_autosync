"""The `push-only` mode, exercised end to end against real git repositories.

The engine is a shell script and had no test at all, which is uncomfortable for
the one tool in the workspace whose whole job is writing to every other repo.
These drive `git_autosync.sh` itself against throwaway repos with a throwaway
remote, so what is asserted is what the script actually does to a working tree.

What push-only is for: a sweep (`git add -A` + commit) is right for a repo
nobody edits between runs. In a repo a person or an agent is working in, it
rolls several unrelated half-finished changes into one commit titled
`autosync: <timestamp>` — worse than not backing them up, because it looks like
history and is painful to unpick later. That is not hypothetical; it happened
three nights running in `imprint`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent / "git_autosync.sh"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def workspace(tmp_path):
    """A bare 'remote' plus a clone, wired the way autosync expects."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)

    repo = tmp_path / "active" / "proj"
    repo.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "t@example.com"),
                       ("commit.gpgsign", "false")):
        git(repo, "config", key, value)
    (repo / "README.md").write_text("start\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return tmp_path, repo


def run_engine(root: Path, config_text: str, *extra: str):
    """Run the real script with its config and log redirected into tmp."""
    config = root / "repos.txt"
    config.write_text(config_text)
    env = {
        **os.environ,
        "AUTOSYNC_CONFIG": str(config),
        "AUTOSYNC_LOG_DIR": str(root / "logs"),
        "AUTOSYNC_STATE_DIR": str(root / "state"),
        "LAB_ACTIVE": str(root / "active"),
        # The gate is not what these tests are about, and a real gitleaks run
        # would make them slow and network/binary dependent.
        "GITLEAKS_CMD": "/usr/bin/true",
    }
    return subprocess.run(["bash", str(ENGINE), *extra],
                          capture_output=True, text=True, env=env)


def _assert_engine_found_the_repo(result):
    """Guard against a green run that never reached the repo.

    An earlier version of this file skipped on "not a git repo", which meant a
    misconfigured fixture reported seven passes-as-skips instead of a failure.
    A test that cannot fail is worse than no test.
    """
    assert "not a git repo" not in result.stdout, (
        "the engine never resolved the fixture repo:\n" + result.stdout[-800:])


class TestPushOnlyLeavesTheWorkingTree:
    def test_uncommitted_work_is_not_committed(self, workspace):
        """The behaviour the mode exists for."""
        root, repo = workspace
        (repo / "half_finished.py").write_text("def broken(:\n")
        before = git(repo, "rev-parse", "HEAD")

        result = run_engine(root, "proj  push-only\n")
        _assert_engine_found_the_repo(result)

        assert git(repo, "rev-parse", "HEAD") == before, (
            "push-only committed the working tree")
        assert "half_finished.py" in git(repo, "status", "--short")

    def test_sweep_does_commit_it(self, workspace):
        """The contrast — proves the test is measuring the mode, not inertia."""
        root, repo = workspace
        (repo / "half_finished.py").write_text("def broken(:\n")
        before = git(repo, "rev-parse", "HEAD")

        result = run_engine(root, "proj\n")
        _assert_engine_found_the_repo(result)

        assert git(repo, "rev-parse", "HEAD") != before, (
            "sweep mode should have committed the working tree")
        assert git(repo, "status", "--short") == ""


class TestPushOnlyStillPushes:
    def test_existing_commits_reach_the_remote(self, workspace):
        """Backup is the point; it just must not invent commits."""
        root, repo = workspace
        (repo / "real.py").write_text("x = 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "a real commit with a real message")

        result = run_engine(root, "proj  push-only\n")
        _assert_engine_found_the_repo(result)

        assert git(repo, "rev-list", "--count", "@{u}..HEAD") == "0", (
            "committed work was not pushed")

    def test_nothing_to_push_is_not_an_error(self, workspace):
        root, repo = workspace
        result = run_engine(root, "proj  push-only\n")
        _assert_engine_found_the_repo(result)
        assert result.returncode == 0


class TestPushOnlyRefusesToPublishABranch:
    def test_an_unpublished_branch_is_left_alone(self, workspace):
        """The second half of the problem.

        autosync pushes whichever branch happens to be checked out. On a repo
        developed in feature branches that means a timer can publish work in
        progress under a name nobody chose to make public. With no upstream,
        push-only declines.
        """
        root, repo = workspace
        git(repo, "checkout", "-qb", "feature/wip")
        (repo / "wip.py").write_text("# not ready\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "wip")

        result = run_engine(root, "proj  push-only\n")
        _assert_engine_found_the_repo(result)

        remote_branches = subprocess.run(
            ["git", "-C", str(repo), "ls-remote", "--heads", "origin"],
            capture_output=True, text=True).stdout
        assert "feature/wip" not in remote_branches, (
            "push-only published an unpublished feature branch")


class TestConfigParsing:
    def test_the_flag_is_not_treated_as_part_of_the_repo_name(self, workspace):
        """`label` is the GUI's row key, so the flag must never leak into it."""
        root, repo = workspace
        result = run_engine(root, "proj  push-only\n")
        _assert_engine_found_the_repo(result)
        assert "proj  push-only" not in result.stdout.replace("REPO: proj  (", "")
        assert "REPO: proj" in result.stdout

    def test_an_unknown_flag_falls_back_to_sweep(self, workspace):
        """A typo must not silently disable backups."""
        root, repo = workspace
        (repo / "f.py").write_text("x\n")
        before = git(repo, "rev-parse", "HEAD")
        result = run_engine(root, "proj  push-onlyy\n")
        _assert_engine_found_the_repo(result)
        assert git(repo, "rev-parse", "HEAD") != before, (
            "a misspelled flag turned off committing")


# ----------------------------------------------------------------------
# A dry-run must not call a repo safe when the push would be rejected
# ----------------------------------------------------------------------
def _remote_moves_ahead(root: Path):
    """Put a commit on the remote that the fixture's clone does not have.

    Through a second clone, because the remote is bare and cannot be committed
    to directly. This is the ordinary way it happens: the same repo pulled or
    pushed on another machine while this one sat still.
    """
    other = root / "other"
    # --branch, because the bare remote was created by `git init --bare` and
    # its HEAD still points at the default branch name, not the `main` the
    # fixture pushed; a plain clone lands with nothing checked out.
    subprocess.run(["git", "clone", "-q", "--branch", "main",
                    str(root / "remote.git"), str(other)], check=True)
    for key, value in (("user.name", "Other"), ("user.email", "o@example.com"),
                       ("commit.gpgsign", "false")):
        git(other, "config", key, value)
    (other / "upstream.md").write_text("from the other machine\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "upstream work")
    git(other, "push", "-q", "origin", "main")


class TestADryRunRefusesToCallABehindRepoSafe:
    """The point of a dry-run is that the real run holds no surprises.

    Being behind the remote is the one state where a push is certain to be
    rejected, so a dry-run that reports OK is worse than no dry-run at all.
    Sweep mode checked for it from the start; push-only never did — and
    push-only is the likelier place to hit it, because those are the repos
    someone is actually working in.
    """

    def test_push_only_says_pull_first(self, workspace):
        root, repo = workspace
        _remote_moves_ahead(root)
        (repo / "local.md").write_text("mine\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "local work")

        result = run_engine(root, "proj  push-only\n", "--dry-run")
        _assert_engine_found_the_repo(result)

        assert "pull first" in result.stdout, (
            "a behind push-only repo was reported safe:\n" + result.stdout[-800:])
        assert "would push 1 commit(s), push-only" not in result.stdout

    def test_sweep_still_says_it_too(self, workspace):
        """The branch that always worked — kept so a refactor cannot trade one
        for the other."""
        root, repo = workspace
        _remote_moves_ahead(root)
        (repo / "local.md").write_text("mine\n")

        result = run_engine(root, "proj\n", "--dry-run")
        _assert_engine_found_the_repo(result)

        assert "pull first" in result.stdout

    def test_a_level_push_only_repo_is_still_reported_ready(self, workspace):
        """The contrast: with nothing on the remote to pull, the new check must
        not get in the way of the answer the dry-run exists to give."""
        root, repo = workspace
        (repo / "local.md").write_text("mine\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "local work")

        result = run_engine(root, "proj  push-only\n", "--dry-run")
        _assert_engine_found_the_repo(result)

        assert "would push 1 commit(s), push-only" in result.stdout
        assert "pull first" not in result.stdout
