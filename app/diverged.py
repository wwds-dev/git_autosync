"""Rebase a diverged repo onto its remote, all or nothing.

`pull --ff-only` refuses when both sides have commits. The fix the dialog used
to print for the user to run by hand is `pull --rebase`; this runs it, but
never leaves a half-finished rebase behind: on any failure, including a
timeout, the rebase is aborted, which puts the branch, the index and (via
--autostash) uncommitted changes back exactly as they were.

Nothing here parses git's prose: git's messages are translated (this machine
runs a German locale) and reworded between versions. Outcomes are read from
state instead: exit codes, the rebase directories, `refs/stash`, unmerged
index entries.

Pushing is not done here. It goes through the engine like any other sync, so
the leak gate still scans what is about to be published.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_ENV = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}

# A rebase rewrites every replayed commit's committer. Pin the same identity
# the engine commits with, so a stray real name/email in a repo's git config
# never reaches GitHub through this path.
_IDENTITY = ("-c", f"user.name={os.environ.get('AUTOSYNC_GIT_NAME', 'wwds-dev')}",
             "-c", "user.email=" + os.environ.get(
                 "AUTOSYNC_GIT_EMAIL", "243015673+wwds-dev@users.noreply.github.com"))


@dataclass
class Divergence:
    ahead: int
    behind: int
    merges: int    # local merge commits; a rebase would flatten them


@dataclass
class RebaseResult:
    ok: bool
    output: str                       # git's own words, for the details pane
    conflicts: list[str] = field(default_factory=list)
    stash_conflict: bool = False      # rebased, but uncommitted changes did not reapply
    abort_failed: bool = False        # could not get back to the starting state


def _git(git: str, path: Path, *args: str, timeout: int | None = 120):
    return subprocess.run([git, "-C", str(path), *args], capture_output=True,
                          text=True, timeout=timeout, env=_ENV)


def _count(git: str, path: Path, spec: str, *extra: str) -> int | None:
    r = _git(git, path, "rev-list", "--count", *extra, spec)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None


def divergence(git: str, path: Path) -> Divergence | None:
    """Both sides' commit counts, or None when this is not a plain divergence
    (no branch, no upstream, unreadable)."""
    ahead = _count(git, path, "@{u}..HEAD")
    behind = _count(git, path, "HEAD..@{u}")
    if ahead is None or behind is None:
        return None
    return Divergence(ahead, behind, _count(git, path, "@{u}..HEAD", "--merges") or 0)


def _stash_ref(git: str, path: Path) -> str:
    return _git(git, path, "rev-parse", "-q", "--verify", "refs/stash").stdout.strip()


def _unmerged(git: str, path: Path) -> list[str]:
    out = _git(git, path, "diff", "--name-only", "--diff-filter=U").stdout
    return sorted(set(out.split()))


def _rebase_in_progress(git: str, path: Path) -> bool:
    git_dir = _git(git, path, "rev-parse", "--absolute-git-dir").stdout.strip()
    return bool(git_dir) and any(
        (Path(git_dir) / d).exists() for d in ("rebase-merge", "rebase-apply"))


def rebase_onto_remote(git: str, path: Path, timeout: int = 600) -> RebaseResult:
    fetch = _git(git, path, "fetch")   # the upstream's remote, whatever it is
    if fetch.returncode != 0:
        return RebaseResult(ok=False, output=(fetch.stdout + fetch.stderr).strip())

    stash_before = _stash_ref(git, path)
    try:
        r = _git(git, path, *_IDENTITY, "rebase", "--autostash", "@{u}",
                 timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        rc = r.returncode
    except subprocess.TimeoutExpired:
        out, rc = f"git rebase did not finish within {timeout} s and was stopped.", -1

    if rc == 0 and not _rebase_in_progress(git, path):
        # The rebase finished. Re-applying the autostash can still have
        # conflicted: git then keeps the changes in a new stash entry and
        # leaves unmerged files behind, yet exits 0.
        clash = _unmerged(git, path)
        return RebaseResult(ok=True, output=out, conflicts=clash,
                            stash_conflict=bool(clash) or _stash_ref(git, path) != stash_before)

    conflicts = _unmerged(git, path)
    abort_failed = False
    if _rebase_in_progress(git, path):
        a = _git(git, path, "rebase", "--abort")
        out += "\n" + (a.stdout + a.stderr).strip()
        abort_failed = a.returncode != 0 or _rebase_in_progress(git, path)
    return RebaseResult(ok=False, output=out.strip(), conflicts=conflicts,
                        abort_failed=abort_failed)


@dataclass
class LinkResult:
    ok: bool
    message: str
    ahead: int = 0
    behind: int = 0


def link_to_origin(git: str, path: Path) -> LinkResult:
    """Point the current branch at the same-named branch on origin.

    Only a config change plus a fetch: nothing is pushed and the working tree
    is untouched. Refuses when GitHub has no such branch, because publishing a
    brand-new branch is a decision push-only mode deliberately leaves to the
    user.
    """
    branch = _git(git, path, "branch", "--show-current").stdout.strip()
    if not branch:
        return LinkResult(False, "This checkout is not on a branch (detached HEAD).")
    remote = _git(git, path, "ls-remote", "--heads", "origin", branch, timeout=60)
    if remote.returncode != 0:
        return LinkResult(False, "Could not reach GitHub: "
                          + (remote.stderr or remote.stdout).strip()[-300:])
    if not remote.stdout.strip():
        return LinkResult(False, f"GitHub has no branch '{branch}' yet. Publishing a new "
                                 "branch is left to you, so nothing was changed.")
    fetch = _git(git, path, "fetch", "origin", branch)
    if fetch.returncode != 0:
        return LinkResult(False, "Fetch failed: " + (fetch.stderr or fetch.stdout).strip()[-300:])
    set_up = _git(git, path, "branch", f"--set-upstream-to=origin/{branch}", branch)
    if set_up.returncode != 0:
        return LinkResult(False, (set_up.stderr or set_up.stdout).strip()[-300:])
    ahead = _count(git, path, "@{u}..HEAD") or 0
    behind = _count(git, path, "HEAD..@{u}") or 0
    return LinkResult(True, f"'{branch}' now tracks origin/{branch}.", ahead, behind)
