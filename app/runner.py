"""QProcess wrapper around git_autosync.sh, plus the output parser."""
import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import paths

STATUS_RE = re.compile(
    r"\|\s+(SYNCED|BLOCKED|SKIP|ERROR|OK)\s+(\S+)(?:\s+\((.*)\))?"
)
COUNTS_RE = re.compile(
    r"synced=(\d+)\s+blocked=(\d+)\s+skipped=(\d+)\s+errors=(\d+)\s+noop=(\d+)"
    r"(?:\s+unlisted=(\d+))?"
)
# One per repo the engine reached: whether everything on this Mac is in the
# backup repo, not just whether the checked-out branch matches origin.
BACKUP_RE = re.compile(r"\|\s+BACKUP\s+(\S+)\s+\((ok|missing|failed)(?::\s*(.*))?\)")
# A repo on disk that is not in the list, so nothing backs it up.
UNLISTED_RE = re.compile(r"\|\s+UNLISTED\s+(\S+)(?:\s+\((.*)\))?")
REPO_HEADER_RE = re.compile(r"REPO: (\S+)")
FILE_RE        = re.compile(r"^File:\s*(.+)$")
RULE_RE        = re.compile(r"^RuleID:\s*(.+)$")
FINGERPRINT_RE = re.compile(r"^Fingerprint:\s*(.+)$")
LINE_RE        = re.compile(r"^Line:\s*(\d+)$")
SECRET_RE      = re.compile(r"^Secret:\s*(.+)$")
ANSI_RE        = re.compile(r"\x1b\[[0-9;]*m")


def parse_summary(text: str) -> dict:
    """Extract per-repo statuses, the final counts line, and (for BLOCKED
    repos) the first gitleaks finding's file/rule from script output.

    Returns {"repos": {name: {"status", "detail"}}, "counts": {...},
    "findings": {name: {"file", "rule"}}, "backup": {name: {"state", "detail"}},
    "unlisted": [{"path", "detail"}]}.
    Per-repo statuses only consider lines from the SUMMARY: block onward, so
    progress chatter earlier in the log can't be mistaken for the final
    result — but findings are extracted from the full per-repo sections
    earlier in the output, since that's the only place gitleaks' verbose
    output appears.
    """
    summary_start = text.find("SUMMARY:")
    tail = text[summary_start:] if summary_start != -1 else text

    repos = {}
    for line in tail.splitlines():
        m = STATUS_RE.search(line)
        if m:
            status, name, detail = m.groups()
            repos[name] = {"status": status, "detail": detail or ""}

    backup = {}
    unlisted = []
    for line in tail.splitlines():
        if m := BACKUP_RE.search(line):
            name, state, detail = m.groups()
            backup[name] = {"state": state, "detail": detail or ""}
        elif m := UNLISTED_RE.search(line):
            unlisted.append({"path": m.group(1), "detail": m.group(2) or ""})

    counts = None
    m = COUNTS_RE.search(tail)
    if m:
        counts = {
            "synced": int(m.group(1)),
            "blocked": int(m.group(2)),
            "skipped": int(m.group(3)),
            "errors": int(m.group(4)),
            "noop": int(m.group(5)),
            "unlisted": int(m.group(6) or 0),
        }

    # gitleaks --verbose prints RuleID: before File: for each finding, so
    # track both independently rather than assuming an order.
    findings = {}
    current_repo = None
    pending_file = pending_rule = pending_fp = pending_line = pending_secret = None
    for line in text.splitlines():
        header = REPO_HEADER_RE.search(line)
        if header:
            current_repo = header.group(1)
            pending_file = pending_rule = pending_fp = pending_line = pending_secret = None
            continue
        if current_repo is None or current_repo in findings:
            continue
        s = line.strip()
        if m := FILE_RE.match(s):
            pending_file = m.group(1).strip()
        if m := RULE_RE.match(s):
            pending_rule = m.group(1).strip()
        if m := FINGERPRINT_RE.match(s):
            pending_fp = m.group(1).strip()
        if m := LINE_RE.match(s):
            pending_line = m.group(1).strip()
        if m := SECRET_RE.match(s):
            pending_secret = ANSI_RE.sub("", m.group(1).strip())
        # Wait until Fingerprint: is seen — it appears after File:/RuleID: in
        # gitleaks output, and the early-exit guard below would skip it otherwise.
        if pending_file is not None and pending_rule is not None and pending_fp is not None:
            findings[current_repo] = {
                "file": pending_file,
                "rule": pending_rule,
                "fingerprint": pending_fp,
                "line": pending_line,
                "secret": pending_secret,
            }

    return {"repos": repos, "counts": counts, "findings": findings,
            "backup": backup, "unlisted": unlisted}


class AutosyncRunner(QObject):
    output_received = Signal(str)
    finished = Signal(int, dict)  # exit_code, parsed summary

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._buffer = ""

    def is_running(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def stop(self) -> None:
        """Terminate the engine subprocess, if one is running.

        Called on quit so a long sync can't keep the app alive.
        """
        if not self.is_running():
            return
        self._process.kill()
        self._process.waitForFinished(1000)

    def start(
        self,
        *,
        dry_run: bool,
        repo: str | None,
        config_path: Path,
        gitleaks_cmd: str,
        create_remote: str | None = None,
        commit_message: str | None = None,
    ):
        if self.is_running():
            return

        bash = paths.find_binary("bash") or "/bin/bash"
        script = str(paths.engine_script())
        args = [script]
        if dry_run:
            args.append("--dry-run")
        if repo:
            args += ["--repo", repo]
        if create_remote:
            args += ["--create-remote", create_remote]

        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PATH", paths.child_env_path())
        env.insert("AUTOSYNC_CONFIG", str(config_path))
        env.insert("AUTOSYNC_LOG_DIR", str(paths.user_log_dir()))
        env.insert("AUTOSYNC_STATE_DIR", str(paths.app_support_dir()))
        env.insert("GITLEAKS_CMD", gitleaks_cmd)
        if create_remote:
            env.insert("GH_CMD", paths.find_gh() or "gh")
        if commit_message:
            env.insert("AUTOSYNC_COMMIT_MSG", commit_message)
        process.setProcessEnvironment(env)

        process.readyReadStandardOutput.connect(self._on_stdout)
        process.readyReadStandardError.connect(self._on_stderr)
        process.finished.connect(self._on_finished)

        self._buffer = ""
        self._process = process
        process.start(bash, args)

    def _on_stdout(self):
        data = bytes(self._process.readAllStandardOutput()).decode(errors="replace")
        self._buffer += data
        self.output_received.emit(data)

    def _on_stderr(self):
        data = bytes(self._process.readAllStandardError()).decode(errors="replace")
        self._buffer += data
        self.output_received.emit(data)

    def _on_finished(self, exit_code: int, _exit_status):
        summary = parse_summary(self._buffer)
        self.finished.emit(exit_code, summary)
