#!/usr/bin/env python3
"""Repo hygiene: is everything committed, pushed and merged?

git_autosync backs work up; it does not say whether the work has *landed*. This
reads every repo in autosync_repos.txt and reports, per repo, what a human would
still have to do: uncommitted files, commits not on the remote, a branch behind
or diverged from it, local branches not merged into the default branch, dirty
linked worktrees. It changes nothing (no add, commit, push or checkout).

    hygiene.py [--fetch] [--out FILE] [--repos FILE] [--lab-root DIR] [--text]

`--fetch` runs `git fetch --prune origin` first, so "behind" and "unpushed" are
true as of now rather than as of the last fetch. Lab Hub leaves it off (it must
not wait on the network); the autosync run turns it on and writes the report
Lab Hub reads.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAB_ROOT = PROJECT_ROOT.parent.parent
GIT_TIMEOUT = 20
FETCH_TIMEOUT = 40
MAX_NAMES = 12


def git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    """Run git in `repo`; (returncode, stdout). Never raises."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, r.stdout


def read_repo_list(path: Path, lab_root: Path) -> list[tuple[str, Path]]:
    """(name, folder) for each repo line, in file order, duplicates dropped."""
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return found
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry = line.split()[0]
        folder = Path(entry).expanduser()
        if not folder.is_absolute():
            folder = lab_root / "active" / entry
        try:
            folder = folder.resolve()
        except OSError:
            continue
        if folder in seen:
            continue
        seen.add(folder)
        found.append((entry, folder))
    return found


def default_repo_file() -> Path:
    """The list autosync really reads: $AUTOSYNC_CONFIG, else the one in
    Application Support, else the copy in this repo (which is only a seed)."""
    env = os.environ.get("AUTOSYNC_CONFIG")
    if env:
        return Path(env).expanduser()
    live = Path.home() / "Library/Application Support/git_autosync/autosync_repos.txt"
    return live if live.is_file() else PROJECT_ROOT / "autosync_repos.txt"


def default_branch(repo: Path) -> str | None:
    rc, out = git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.strip():
        return out.strip().split("/", 1)[-1]
    for name in ("main", "master"):
        if git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{name}")[0] == 0:
            return name
    return None


def count_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def inspect(name: str, folder: Path, fetch: bool) -> dict:
    row: dict = {"name": name, "path": str(folder), "problems": [], "notes": []}
    if not (folder / ".git").exists():
        row["error"] = "not a git repository"
        return row

    if fetch:
        rc, _ = git(folder, "fetch", "--quiet", "--prune", "origin", timeout=FETCH_TIMEOUT)
        row["fetched"] = rc == 0
        if rc != 0:
            row["notes"].append("fetch failed - counts are as of the last fetch")

    rc, branch = git(folder, "symbolic-ref", "-q", "--short", "HEAD")
    branch = branch.strip() if rc == 0 else ""
    row["branch"] = branch or "(detached)"
    default = default_branch(folder)
    row["default_branch"] = default

    dirty = count_lines(git(folder, "status", "--porcelain=v1")[1])
    row["dirty"] = dirty
    if dirty:
        row["problems"].append(f"{dirty} uncommitted file{'s' if dirty != 1 else ''}")

    # Ahead/behind against what this branch tracks, else origin/<branch>.
    upstream = ""
    if branch:
        rc, out = git(folder, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if rc == 0:
            upstream = out.strip()
        elif git(folder, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}")[0] == 0:
            upstream = f"origin/{branch}"
    row["upstream"] = upstream or None
    ahead = behind = 0
    if upstream:
        rc, out = git(folder, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
        if rc == 0 and len(out.split()) == 2:
            ahead, behind = (int(n) for n in out.split())
    else:
        # No remote copy of this branch at all: everything on it that no remote
        # branch has is unpushed.
        rc, out = git(folder, "rev-list", "--count", "HEAD", "--not", "--remotes")
        if rc == 0 and out.strip().isdigit():
            ahead = int(out.strip())
        if branch and ahead:
            row["notes"].append(f"{branch} has no remote branch")
    row["ahead"], row["behind"] = ahead, behind
    if ahead and behind:
        row["problems"].append(f"diverged: {ahead} unpushed, {behind} behind")
    else:
        if ahead:
            row["problems"].append(f"{ahead} unpushed commit{'s' if ahead != 1 else ''}")
        if behind:
            row["problems"].append(f"{behind} behind {upstream}")

    # Local branches whose commits are not in the default branch.
    unmerged: list[str] = []
    if default:
        base = f"origin/{default}"
        if git(folder, "rev-parse", "-q", "--verify", f"refs/remotes/{base}")[0] != 0:
            base = default
        rc, out = git(folder, "branch", "--no-merged", base, "--format=%(refname:short)")
        if rc == 0:
            unmerged = [b.strip() for b in out.splitlines() if b.strip() and b.strip() != default]
    row["unmerged"] = unmerged[:MAX_NAMES]
    row["unmerged_count"] = len(unmerged)
    if unmerged:
        row["problems"].append(
            f"{len(unmerged)} branch{'es' if len(unmerged) != 1 else ''} not merged into {default}"
        )

    # Linked worktrees: another session's unfinished work lives here.
    worktrees: list[dict] = []
    rc, out = git(folder, "worktree", "list", "--porcelain")
    if rc == 0:
        blocks = [b for b in out.split("\n\n") if b.strip()]
        for block in blocks[1:]:  # the first is the main checkout
            fields = {k: v for k, _, v in (ln.partition(" ") for ln in block.splitlines())}
            wt = fields.get("worktree")
            if not wt or "prunable" in block or not Path(wt).is_dir():
                continue
            n = count_lines(git(Path(wt), "status", "--porcelain=v1")[1])
            worktrees.append({"path": wt, "branch": fields.get("branch", "").removeprefix("refs/heads/"),
                              "dirty": n})
    row["worktrees"] = worktrees
    dirty_wt = [w for w in worktrees if w["dirty"]]
    if dirty_wt:
        row["problems"].append(
            f"{len(dirty_wt)} worktree{'s' if len(dirty_wt) != 1 else ''} with uncommitted work"
        )

    stashes = count_lines(git(folder, "stash", "list")[1])
    row["stashes"] = stashes
    if stashes:
        row["notes"].append(f"{stashes} stash{'es' if stashes != 1 else ''}")
    return row


def build_report(repos: list[tuple[str, Path]], fetch: bool) -> dict:
    rows = [inspect(name, folder, fetch) for name, folder in repos]
    return {
        "generated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "fetched": fetch,
        "attention": sum(1 for r in rows if r.get("problems") or r.get("error")),
        "repos": rows,
    }


def render_text(report: dict) -> str:
    lines = []
    for r in report["repos"]:
        flag = "!!" if r.get("problems") or r.get("error") else "ok"
        detail = r.get("error") or "; ".join(r["problems"]) or "clean, pushed, merged"
        lines.append(f"[{flag}] {r['name']} ({r.get('branch', '?')}) - {detail}")
    lines.append(f"{report['attention']} of {len(report['repos'])} repos need attention")
    return "\n".join(lines)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".hygiene-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch", action="store_true", help="git fetch --prune origin first")
    ap.add_argument("--out", type=Path, help="also write the JSON report here")
    ap.add_argument("--repos", type=Path, help="repo list (default: autosync_repos.txt)")
    ap.add_argument("--lab-root", type=Path, default=DEFAULT_LAB_ROOT)
    ap.add_argument("--text", action="store_true", help="print a readable summary, not JSON")
    args = ap.parse_args(argv)

    lab_root = args.lab_root.expanduser().resolve()
    repo_file = args.repos or default_repo_file()
    report = build_report(read_repo_list(repo_file, lab_root), args.fetch)
    text = json.dumps(report, indent=1)
    if args.out:
        write_atomic(args.out, text + "\n")
    print(render_text(report) if args.text else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
