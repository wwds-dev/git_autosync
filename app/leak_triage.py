"""What to do about a repo the leak-gate blocked — the parts that aren't Qt.

The gate itself only says "blocked". Deciding what that means for the person
looking at it needs three more facts, all worked out here so they can be
tested against real repos:

  * where the finding lives — uncommitted changes, local commits that were
    never pushed, or history that is already on GitHub. That decides whether a
    real secret is a quick local fix or a key that has to be rotated;
  * the actual flagged value. The engine runs gitleaks with --redact, so the
    only "secret" the GUI ever parses is the literal text REDACTED. A history
    purge built on that rewrote nothing, force-pushed anyway, and reported
    success. The value has to be looked up again, from the one commit;
  * edits to .gitleaksignore that leave its comments alone, so an allowlist
    entry can be added and taken back without touching anything else.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

UNCOMMITTED = "uncommitted"
LOCAL = "local"
PUBLISHED = "published"

WHERE_TEXT = {
    UNCOMMITTED: "in uncommitted changes",
    LOCAL: "in local commits not yet pushed",
    PUBLISHED: "in history already on GitHub",
}

_COMMIT_FP_RE = re.compile(r"^([0-9a-f]{40}):")


class PurgeError(Exception):
    """A history purge stopped before it could do harm; the message says why."""


def _git(repo: Path, *args: str, git: str = "git",
         check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run([git, "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


# ── .gitleaksignore ───────────────────────────────────────────────

def ignore_entries(path: Path) -> list[str]:
    """Fingerprints in the file — comment and blank lines skipped."""
    if not path.exists():
        return []
    return [s for s in (line.strip() for line in path.read_text().splitlines())
            if s and not s.startswith("#")]


def append_ignore_entry(path: Path, fp: str) -> bool:
    """Add one fingerprint. False if it was already there."""
    text = path.read_text() if path.exists() else ""
    if fp in (line.strip() for line in text.splitlines()):
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + fp + "\n")
    return True


def remove_ignore_entry(path: Path, fp: str) -> bool:
    """Remove one fingerprint, keeping every other line. False if absent."""
    if not path.exists():
        return False
    lines = path.read_text().splitlines(keepends=True)
    kept = [line for line in lines if line.strip() != fp]
    if len(kept) == len(lines):
        return False
    path.write_text("".join(kept))
    return True


# ── Where the finding lives ───────────────────────────────────────

def finding_commit(finding: dict) -> str | None:
    """The commit a fingerprint is pinned to; None for a staged-changes finding.

    History findings are `<commit>:<file>:<rule>:<line>`; a scan of staged
    changes has no commit yet and prints `<file>:<rule>:<line>`.
    """
    m = _COMMIT_FP_RE.match(finding.get("fingerprint") or "")
    return m.group(1) if m else None


def is_published(repo: Path, commit: str, git: str = "git") -> bool:
    """True if any remote-tracking branch contains the commit (as of the last fetch).

    An error — an unknown commit, a broken repo — counts as published: the
    wrong answer in that direction only makes someone rotate a key they
    didn't strictly need to.
    """
    r = _git(repo, "branch", "-r", "--contains", commit, git=git)
    return r.returncode != 0 or bool(r.stdout.strip())


def locate(repo: Path, finding: dict, git: str = "git") -> str:
    commit = finding_commit(finding)
    if commit is None:
        return UNCOMMITTED
    return PUBLISHED if is_published(repo, commit, git=git) else LOCAL


def describe_block(name: str, finding: dict, where: str) -> str:
    """Rich-text explanation for the Fix leak… dialog."""
    loc = finding.get("file", "?")
    if finding.get("line"):
        loc += f":{finding['line']}"
    rule = finding.get("rule") or "unknown rule"
    head = (
        f"<b>{name}</b> is blocked: gitleaks found something that looks like a "
        f"secret <b>{WHERE_TEXT[where]}</b>.<br>"
        f"<code>{loc}</code> &nbsp;·&nbsp; rule <code>{rule}</code><br><br>"
        "Nothing was committed or pushed for this repo. The block stays until "
        "the finding is either removed or allowlisted, then a dry-run clears it."
        "<br><br><b>Is it a real secret?</b><br>"
        "• <b>No — false positive</b> (test dummy, example value, a name that "
        "only looks like a key): allowlist it. That only edits "
        f"<code>{name}/.gitleaksignore</code> locally and can be undone.<br>"
    )
    if where == UNCOMMITTED:
        tail = ("• <b>Yes</b>: delete it from the file (move it to an env var or "
                "keychain). It was never committed, so nothing else is needed.")
    elif where == LOCAL:
        tail = ("• <b>Yes</b>: it is only in commits on this Mac. Remove it from "
                "history below — no force-push is needed, and the next sync "
                "publishes the cleaned commits.")
    else:
        tail = ("• <b>Yes</b>: it is already public. <b>Rotate or revoke the key "
                "first</b> — anyone may have copied it. Then remove it from "
                "history below; that force-pushes and cannot be undone on GitHub.")
    return head + tail


# ── The real flagged value ────────────────────────────────────────

def resolve_secret(repo: Path, finding: dict, gitleaks_cmd: str,
                   git: str = "git") -> str | None:
    """Look the flagged value up again by scanning just its commit, unredacted.

    Returns None unless the fingerprint matches exactly and the value really
    appears in that commit's copy of the file — a purge must never run on a
    guess. The value goes through a private temp file only; it is never
    printed or logged.
    """
    commit = finding_commit(finding)
    fp = finding.get("fingerprint")
    if not commit or not fp:
        return None
    fd, report = tempfile.mkstemp(prefix="autosync_finding_", suffix=".json")
    os.close(fd)
    try:
        subprocess.run(
            [gitleaks_cmd, "git", "--no-banner", "--log-opts", f"-1 {commit}",
             "--report-format", "json", "--report-path", report, str(repo)],
            capture_output=True, text=True, timeout=300,
        )
        try:
            items = json.loads(Path(report).read_text() or "[]")
        except (OSError, ValueError):
            return None
    finally:
        Path(report).unlink(missing_ok=True)

    match = next((i for i in items if i.get("Fingerprint") == fp), None)
    secret = (match or {}).get("Secret") or ""
    if not secret or secret == "REDACTED":
        return None
    blob = _git(repo, "show", f"{commit}:{match.get('File', '')}", git=git)
    if blob.returncode != 0 or secret not in blob.stdout:
        return None
    return secret


def commits_containing(repo: Path, secret: str, *refs: str, git: str = "git") -> list[str]:
    r = _git(repo, "log", *(refs or ("--all",)), "-S", secret, "--format=%H", git=git)
    return r.stdout.split()


def published_with(repo: Path, secret: str, git: str = "git") -> bool:
    """True if any remote-tracking branch has a commit adding or removing the value."""
    return bool(commits_containing(repo, secret, "--remotes", git=git))


# ── The purge ─────────────────────────────────────────────────────

def purge_secret(repo: Path, secret: str, filter_repo: str, gitleaks_cmd: str,
                 git: str = "git", push: bool = True) -> str:
    """Replace `secret` with [REDACTED] in every commit, verify, then push.

    Stops (PurgeError) rather than guessing:
      * a dirty working tree — filter-repo resets it, which would lose work;
      * the value still present anywhere afterwards — nothing is pushed;
      * any other finding left in history — the gate only ever reported the
        first one, and a force-push must not publish a second secret.

    filter-repo drops the `origin` remote and branch upstreams on a full
    rewrite, so both are saved and put back. Only branches origin already had
    are force-pushed, each leased to the exact commit origin had before, so a
    push that raced this one is refused instead of overwritten.
    """
    if _git(repo, "status", "--porcelain", git=git).stdout.strip():
        raise PurgeError(
            "The working tree has uncommitted changes. Rewriting history resets "
            "the working tree, so commit or stash them first, then try again.")

    origin = _git(repo, "remote", "get-url", "origin", git=git).stdout.strip()
    upstreams = _git(repo, "config", "--get-regexp",
                     r"^branch\..*\.(remote|merge)$", git=git).stdout.splitlines()
    leases = {}
    for line in _git(repo, "for-each-ref", "refs/remotes/origin",
                     "--format=%(refname:strip=3) %(objectname)", git=git).stdout.splitlines():
        branch, _, sha = line.partition(" ")
        if branch and branch != "HEAD":
            leases[branch] = sha

    fd, replacements = tempfile.mkstemp(prefix="autosync_replace_", suffix=".txt")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"literal:{secret}==>[REDACTED]\n")
        r = subprocess.run([filter_repo, "--replace-text", replacements, "--force"],
                           cwd=str(repo), capture_output=True, text=True)
    finally:
        Path(replacements).unlink(missing_ok=True)
    if r.returncode != 0:
        raise PurgeError(f"git-filter-repo failed:\n{(r.stderr or r.stdout).strip()}")

    if origin and _git(repo, "remote", "get-url", "origin", git=git).returncode != 0:
        _git(repo, "remote", "add", "origin", origin, git=git, check=True)
    for line in upstreams:
        key, _, value = line.partition(" ")
        _git(repo, "config", key, value, git=git)

    left = commits_containing(repo, secret, git=git)
    if left:
        raise PurgeError(
            f"The value is still in {len(left)} commit(s) after the rewrite "
            f"(first: {left[0][:12]}). Nothing was pushed.")

    pushed = []
    if push and origin:
        scan = subprocess.run([gitleaks_cmd, "git", "--no-banner", "--redact", str(repo)],
                              capture_output=True, text=True)
        if scan.returncode != 0:
            raise PurgeError(
                "History was cleaned locally, but gitleaks still finds other "
                "secrets in this repo, so nothing was pushed. Run a dry-run to "
                "see the next finding.")
        specs = []
        for branch, sha in leases.items():
            if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}",
                    git=git).returncode == 0:
                specs += [f"--force-with-lease=refs/heads/{branch}:{sha}",
                          f"refs/heads/{branch}:refs/heads/{branch}"]
                pushed.append(branch)
        if specs:
            leases_args = [s for s in specs if s.startswith("--")]
            refspecs = [s for s in specs if not s.startswith("--")]
            p = _git(repo, "push", *leases_args, "origin", *refspecs, git=git)
            if p.returncode != 0:
                raise PurgeError(
                    "History was cleaned locally, but the force-push failed:\n"
                    f"{(p.stderr or p.stdout).strip()}")
    if origin:
        _git(repo, "fetch", "-q", "origin", git=git)

    if pushed:
        return f"Cleaned history force-pushed to origin ({', '.join(pushed)})."
    return "History cleaned locally. Nothing was force-pushed."
