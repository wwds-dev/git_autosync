"""Turn a run's failures into plain advice with a usable next step.

The engine already names causes ("behind remote by 6 - pull first"), but that
sentence only reached a tooltip and the log. A failing run should say what went
wrong, what it means, and offer the thing that fixes it — without the user
having to know that "Fix leak…" is where a blocked repo gets resolved.

Each entry is (headline, explanation, action label, action key). The action key
is resolved by the window to one of its existing handlers; nothing here
performs an action itself, so this module stays testable and side-effect free.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Advice:
    repo: str
    headline: str
    explanation: str
    action_label: str | None = None
    action: str | None = None        # "leak" | "pull" | "create_repo" | "remove" | None


def advise(name: str, status: str, detail: str) -> Advice | None:
    """Advice for one repo's outcome, or None when nothing is wrong."""
    d = (detail or "").lower()

    if status == "BLOCKED":
        return Advice(
            name,
            f"{name} — a secret was found, so nothing was pushed",
            "gitleaks matched something that looks like a credential. Until it "
            "is dealt with, this repo is skipped entirely — that is the gate "
            "working, not a malfunction.\n\n"
            "Open the triage to see the file and line, then either remove it "
            "from history (if it is a real secret) or allowlist the finding "
            "(if it is a false positive, like a SQL string).",
            "Fix leak…", "leak")

    if status == "ERROR":
        if "pull first" in d or "behind remote" in d:
            return Advice(
                name,
                f"{name} — the remote has commits you do not have",
                "Someone or something else pushed to this repo, so your branch "
                "is behind and git refuses the push. Nothing is lost.\n\n"
                "Pull fast-forwards the branch so the next sync can push. If "
                "both sides have commits it will refuse and say so, rather "
                "than merging behind your back.",
                "Pull", "pull")
        if "not authorised" in d or "auth" in d or "credential" in d:
            return Advice(
                name,
                f"{name} — GitHub refused the push",
                "The push was rejected as unauthorised. Usually the gh "
                "credentials have expired or the account lost access to this "
                "repo.\n\nRun  gh auth status  and, if needed,  gh auth login.",
                None, None)
        if "network" in d or "timed out" in d or "transient" in d:
            return Advice(
                name,
                f"{name} — could not reach GitHub",
                "The push failed partway on the network. This is transient and "
                "nothing is broken; the next sync will retry. Large first "
                "pushes time out most often.",
                None, None)
        if "not found" in d or "renamed" in d:
            return Advice(
                name,
                f"{name} — the remote repository is missing",
                "GitHub has no repo at the URL this checkout points to. It was "
                "renamed, deleted, or the remote is wrong.\n\n"
                "Check with  git -C <repo> remote -v  and repoint it with "
                "git remote set-url origin <url>.",
                None, None)
        if "branch protection" in d or "protected" in d:
            return Advice(
                name,
                f"{name} — a branch protection rule rejected the push",
                "GitHub's settings for this repo forbid pushing straight to "
                "this branch. Push a branch and open a pull request, or relax "
                "the rule in the repo's settings.",
                None, None)
        if "scanner" in d:
            return Advice(
                name,
                f"{name} — the secret scanner failed",
                "gitleaks did not complete, so the repo was skipped rather "
                "than pushed unchecked. That is deliberate: a scanner that "
                "cannot run must not be read as 'clean'.\n\n"
                "Check that gitleaks is installed and runs:  gitleaks version.",
                None, None)
        return Advice(name, f"{name} — sync failed",
                      detail or "No reason was recorded. The log has the raw "
                                "git output.", None, None)

    if status == "SKIP":
        if "no github remote" in d:
            return Advice(
                name,
                f"{name} — not on GitHub yet",
                "This repo has no 'origin' remote, so there is nowhere to push "
                "it and nothing is backing it up.\n\n"
                "Create a GitHub repo for it — it is scanned for secrets "
                "before anything is published.",
                "Create GitHub Repo…", "create_repo")
        if "not a git repo" in d:
            return Advice(
                name,
                f"{name} — not a git repository",
                "The path in your list is not a git checkout. It was probably "
                "moved or deleted.\n\nUse 'Find repos…' to fix the path or "
                "drop the entry.",
                "Remove from list", "remove")
        return Advice(name, f"{name} — skipped", detail or "Skipped.", None, None)

    return None


def collect(summary_repos: dict) -> list[Advice]:
    """Advice for every repo in a run that needs attention, worst first."""
    order = {"BLOCKED": 0, "ERROR": 1, "SKIP": 2}
    out = []
    for name, info in summary_repos.items():
        a = advise(name, info.get("status", ""), info.get("detail", ""))
        if a:
            out.append((order.get(info.get("status"), 9), name, a))
    return [a for _, _, a in sorted(out, key=lambda x: (x[0], x[1]))]
