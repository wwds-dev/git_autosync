"""Path resolution for the git_autosync GUI.

Finder-launched .app bundles get a minimal PATH (no /opt/homebrew/bin), and a
PyInstaller bundle is read-only, so every path used at runtime has to be
resolved explicitly rather than relied on from the environment.
"""
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Extra directories to search for CLI tools, beyond whatever PATH the app was
# launched with (Finder-launched apps don't inherit the shell's PATH).
EXTRA_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def bundle_root() -> Path:
    """Directory containing bundled data files (engine script, etc.)."""
    return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))


def engine_script() -> Path:
    return bundle_root() / "git_autosync.sh"


def readme_path() -> Path:
    return bundle_root() / "README.md"


def child_env_path() -> str:
    """A PATH string that includes the usual Homebrew/system locations."""
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(":") if p]
    for d in EXTRA_BIN_DIRS:
        if d not in parts:
            parts.append(d)
    return ":".join(parts)


def find_binary(name: str) -> str | None:
    """Resolve a binary by name, checking EXTRA_BIN_DIRS then PATH."""
    for d in EXTRA_BIN_DIRS:
        candidate = Path(d) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    return found


def find_gitleaks() -> str | None:
    return find_binary("gitleaks")


def find_git() -> str | None:
    return find_binary("git")


def find_gh() -> str | None:
    return find_binary("gh")


def lab_active_dir() -> Path:
    return Path(os.environ.get("LAB_ACTIVE", str(Path.home() / "Documents" / "lab" / "active")))


_SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "build", "dist",
              "__pycache__", ".claude"}


def discover_repos(max_depth: int = 3) -> list[str]:
    """Every git repo under lab_active_dir(), as paths relative to it.

    Nested repos are normal here — a companion app under its parent project is
    still its own repo — so this recurses instead of scanning one level. Returns
    e.g. ["backup_manager", "toolbox/convert_epub", "imprint/vidforge"].
    """
    base = lab_active_dir()
    if not base.is_dir():
        return []
    found: list[str] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for child in sorted(d.iterdir()):
            if not child.is_dir() or child.name in _SKIP_DIRS:
                continue
            if (child / ".git").is_dir():
                found.append(str(child.relative_to(base)))
            walk(child, depth + 1)

    walk(base, 1)
    return found


def repo_exists(entry: str) -> bool:
    """True if a config entry still resolves to a git repo on disk."""
    return (resolve_entry(entry) / ".git").is_dir()


def resolve_entry(entry: str) -> Path:
    """Config entry -> filesystem path, matching the shell engine's rules."""
    e = entry.strip()
    if e.startswith("~"):
        return Path(e).expanduser()
    if e.startswith("/"):
        return Path(e)
    return lab_active_dir() / e


def has_remote(entry: str) -> bool:
    git = find_git() or "git"
    path = resolve_entry(entry)
    return subprocess_run([git, "-C", str(path), "remote", "get-url", "origin"]) == 0


def repos_without_remote() -> list[Path]:
    """Git repos under lab_active_dir() that have no 'origin' remote configured —
    candidates for the "Create GitHub repo" flow. Recurses, so nested repos
    (imprint/vidforge, sentinel/vpn_agent, toolbox/*) are seen too."""
    base = lab_active_dir()
    return [base / rel for rel in discover_repos() if not has_remote(rel)]


def last_commit_time(entry: str):
    """When this repo last changed, from git itself.

    The app's own bookkeeping only knows when *it* last pushed, which says
    nothing once commits arrive by other means. git is the source of truth for
    "has anything happened here lately".
    """
    from datetime import datetime
    import subprocess

    git = find_git() or "git"
    try:
        r = subprocess.run(
            [git, "-C", str(resolve_entry(entry)), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return None
    out = r.stdout.strip()
    if r.returncode != 0 or not out.isdigit():
        return None
    return datetime.fromtimestamp(int(out))


def readme_updated() -> str | None:
    """When the bundled README last changed.

    Prefers the commit date (what the docs were last edited), falling back to
    the file's mtime for a frozen bundle, where there is no git checkout.
    """
    from datetime import datetime
    import subprocess

    path = readme_path()
    if not path.exists():
        return None
    git = find_git() or "git"
    try:
        r = subprocess.run(
            [git, "-C", str(PROJECT_ROOT), "log", "-1", "--format=%cd",
             "--date=format:%Y-%m-%d %H:%M", "--", "README.md"],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def repo_slug(name: str) -> str | None:
    """owner/repo for a local repo, read from its 'origin' remote.

    The local directory name is not reliably the GitHub repo name, and the
    owner may not be the authenticated user (forks, org repos).
    """
    import re
    import subprocess

    git = find_git() or "git"
    path = lab_active_dir() / name
    try:
        r = subprocess.run(
            [git, "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def subprocess_run(cmd: list[str]) -> int:
    import subprocess

    return subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode


def app_support_dir() -> Path:
    d = Path.home() / "Library" / "Application Support" / "git_autosync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_log_dir() -> Path:
    d = app_support_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def last_sync_path() -> Path:
    return app_support_dir() / "last_sync.txt"


def read_last_sync() -> str | None:
    """Written by git_autosync.sh itself (AUTOSYNC_STATE_DIR) after any real
    run, whether triggered by the GUI or the scheduled background job."""
    p = last_sync_path()
    return p.read_text().strip() if p.exists() else None


def last_success_path() -> Path:
    return app_support_dir() / "last_success.txt"


def read_last_success() -> str | None:
    """When a real run last completed with nothing blocked and nothing errored.

    Distinct from read_last_sync(), which only says a run happened — a run that
    failed to push still updates that one.
    """
    p = last_success_path()
    return p.read_text().strip() if p.exists() else None


def last_status_path() -> Path:
    return app_support_dir() / "last_status.txt"


def read_last_status() -> str | None:
    """'ok' or 'attention', written alongside last_sync.txt. None if no real
    run has completed yet."""
    p = last_status_path()
    return p.read_text().strip() if p.exists() else None


def user_config_path() -> Path:
    """Writable repo-list location, seeded from the bundled default on first run."""
    cfg = app_support_dir() / "autosync_repos.txt"
    if not cfg.exists():
        seed = bundle_root() / "autosync_repos.txt"
        if seed.exists():
            cfg.write_text(seed.read_text())
        else:
            cfg.write_text(
                "# autosync_repos.txt — one repo per line, bare name resolves\n"
                "# under ~/Documents/lab/active/. Lines starting with # are ignored.\n"
            )
    return cfg
