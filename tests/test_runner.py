from app.runner import parse_summary

# Full realistic output including Fingerprint: so the parser records the finding
SAMPLE = """\
2026-06-27 02:00:00 | === git_autosync start (dry-run=0) ===
2026-06-27 02:00:00 | gitleaks: 8.18.0
---------------------------------------------
2026-06-27 02:00:00 | REPO: backup_manager  (/Users/as/Documents/lab/active/backup_manager)
2026-06-27 02:00:00 |   scanning for secrets...
2026-06-27 02:00:01 |   clean .
2026-06-27 02:00:01 |   committed changes.
2026-06-27 02:00:02 |   pushed to origin/main.
---------------------------------------------
2026-06-27 02:00:02 | REPO: sentinel_ai  (/Users/as/Documents/lab/active/sentinel_ai)
2026-06-27 02:00:02 |   scanning for secrets...
Finding:     ...REDACTED...
Secret:      REDACTED
RuleID:      aws-access-token
Entropy:     3.884184
File:        config/secrets.txt
Line:        2
Commit:      abc123def456
Author:      Netrunner3000
Email:       243015673+Netrunner3000@users.noreply.github.com
Date:        2026-06-27T02:00:00Z
Fingerprint: abc123def456:config/secrets.txt:aws-access-token:2

2026-06-27 02:00:02 |   BLOCKED: gitleaks found a secret in your changes. Nothing committed or pushed.
---------------------------------------------
2026-06-27 02:00:03 | REPO: vpn_agent  (/Users/as/Documents/lab/active/vpn_agent)
2026-06-27 02:00:03 |   SKIP: no 'origin' remote (run the GitHub setup script first)
---------------------------------------------
2026-06-27 02:00:03 | SUMMARY:
2026-06-27 02:00:03 |    SYNCED  backup_manager
2026-06-27 02:00:03 |    BLOCKED sentinel_ai  (secret in changes - see log)
2026-06-27 02:00:03 |    SKIP    vpn_agent  (no GitHub remote)
2026-06-27 02:00:03 | synced=1 blocked=1 skipped=1 errors=0 noop=0
2026-06-27 02:00:03 | === git_autosync end ===
"""


def test_parses_per_repo_statuses():
    result = parse_summary(SAMPLE)
    repos = result["repos"]
    assert repos["backup_manager"]["status"] == "SYNCED"
    assert repos["sentinel_ai"]["status"] == "BLOCKED"
    assert repos["sentinel_ai"]["detail"] == "secret in changes - see log"
    assert repos["vpn_agent"]["status"] == "SKIP"
    assert repos["vpn_agent"]["detail"] == "no GitHub remote"


def test_parses_counts():
    result = parse_summary(SAMPLE)
    assert result["counts"] == {
        "synced": 1,
        "blocked": 1,
        "skipped": 1,
        "errors": 0,
        "noop": 0,
    }


def test_ignores_pre_summary_repo_mentions():
    result = parse_summary(SAMPLE)
    assert len(result["repos"]) == 3


def test_handles_dry_run_ok_status():
    text = (
        "2026-06-27 02:00:00 | SUMMARY:\n"
        "2026-06-27 02:00:00 |    OK      backup_manager  (dry-run: would sync)\n"
        "2026-06-27 02:00:00 | synced=0 blocked=0 skipped=0 errors=0 noop=1\n"
    )
    result = parse_summary(text)
    assert result["repos"]["backup_manager"]["status"] == "OK"
    assert result["counts"]["noop"] == 1


def test_no_summary_returns_empty():
    result = parse_summary("nothing relevant here")
    assert result["repos"] == {}
    assert result["counts"] is None


def test_extracts_finding_for_blocked_repo():
    result = parse_summary(SAMPLE)
    f = result["findings"]["sentinel_ai"]
    assert f["file"] == "config/secrets.txt"
    assert f["rule"] == "aws-access-token"
    assert f["line"] == "2"
    assert f["fingerprint"] == "abc123def456:config/secrets.txt:aws-access-token:2"
    assert "backup_manager" not in result["findings"]


def test_finding_requires_fingerprint_line():
    # A finding without Fingerprint: must NOT be recorded — the early-exit
    # guard would otherwise skip the Fingerprint: line, leaving it as None.
    text = (
        "REPO: myrepo  (/x/myrepo)\n"
        "RuleID:      generic-api-key\n"
        "File:        a.txt\n"
        "Line:        5\n"
        # No Fingerprint: line
        "SUMMARY:\n"
        "   BLOCKED myrepo  (secret in changes - see log)\n"
        "synced=0 blocked=1 skipped=0 errors=0 noop=0\n"
    )
    result = parse_summary(text)
    assert result["findings"] == {}


def test_finding_with_fingerprint_captured():
    text = (
        "REPO: myrepo  (/x/myrepo)\n"
        "RuleID:      generic-api-key\n"
        "File:        a.txt\n"
        "Line:        5\n"
        "Fingerprint: deadbeef:a.txt:generic-api-key:5\n"
        "SUMMARY:\n"
        "   BLOCKED myrepo  (secret in changes - see log)\n"
        "synced=0 blocked=1 skipped=0 errors=0 noop=0\n"
    )
    result = parse_summary(text)
    f = result["findings"]["myrepo"]
    assert f["file"] == "a.txt"
    assert f["rule"] == "generic-api-key"
    assert f["line"] == "5"
    assert f["fingerprint"] == "deadbeef:a.txt:generic-api-key:5"


def test_secret_field_captured_and_ansi_stripped():
    text = (
        "REPO: myrepo  (/x/myrepo)\n"
        "RuleID:      generic-api-key\n"
        "File:        a.txt\n"
        "Line:        1\n"
        "Secret:      \x1b[1;3mmy_secret_value\x1b[0m\n"
        "Fingerprint: deadbeef:a.txt:generic-api-key:1\n"
        "SUMMARY:\n"
        "   BLOCKED myrepo\n"
        "synced=0 blocked=1 skipped=0 errors=0 noop=0\n"
    )
    result = parse_summary(text)
    assert result["findings"]["myrepo"]["secret"] == "my_secret_value"


def test_no_origin_repo_skipped_not_errored():
    # A repo with no GitHub remote must produce SKIP, never ERROR —
    # skipping is intentional, erroring would incorrectly flag the run.
    text = (
        "2026-06-27 02:00:03 | REPO: new_project  (/x/new_project)\n"
        "2026-06-27 02:00:03 |   SKIP: no 'origin' remote (run the GitHub setup script first)\n"
        "2026-06-27 02:00:03 | SUMMARY:\n"
        "2026-06-27 02:00:03 |    SKIP    new_project  (no GitHub remote)\n"
        "2026-06-27 02:00:03 | synced=0 blocked=0 skipped=1 errors=0 noop=0\n"
    )
    result = parse_summary(text)
    assert result["repos"]["new_project"]["status"] == "SKIP"
    assert result["counts"]["errors"] == 0
    assert result["counts"]["skipped"] == 1


def test_gitleaks_error_treated_as_blocked_not_clean():
    # If gitleaks fails to run (exit 2+), the repo must be ERROR/BLOCKED,
    # never SYNCED — the tool must fail closed, not open.
    text = (
        "2026-06-27 02:00:00 | REPO: myrepo  (/x/myrepo)\n"
        "2026-06-27 02:00:00 |   ERROR: gitleaks failed to run on staged changes. Skipping for safety.\n"
        "2026-06-27 02:00:00 | SUMMARY:\n"
        "2026-06-27 02:00:00 |    ERROR   myrepo  (gitleaks failed)\n"
        "2026-06-27 02:00:00 | synced=0 blocked=0 skipped=0 errors=1 noop=0\n"
    )
    result = parse_summary(text)
    status = result["repos"]["myrepo"]["status"]
    assert status == "ERROR", f"Expected ERROR, got {status!r} — tool must fail closed"
    assert result["counts"]["synced"] == 0
    assert result["counts"]["errors"] == 1


def test_unpushed_commits_reported_as_would_push():
    """A repo with no file changes but unpushed commits is not "clean" —
    the dry-run must say a push is pending so the GUI can badge it."""
    text = (
        "2026-09-09 17:00:00 | SUMMARY:\n"
        "2026-09-09 17:00:00 |    OK      imprint  (dry-run: would push 30 unpushed commit(s))\n"
        "2026-09-09 17:00:00 | synced=0 blocked=0 skipped=0 errors=0 noop=1\n"
    )
    summary = parse_summary(text)
    info = summary["repos"]["imprint"]
    assert info["status"] == "OK"
    assert "would push" in info["detail"]


def test_summary_keys_are_config_entries_not_basenames():
    """The GUI keys rows by config entry, so the engine must report the entry.
    Reporting the basename made every nested repo read back as 'never synced'."""
    text = (
        "2026-09-09 20:06:06 | SUMMARY:\n"
        "2026-09-09 20:06:06 |    SYNCED  sentinel_fork/agents/chat_agent\n"
        "2026-09-09 20:06:06 |    SYNCED  sonar/sonar/macro\n"
        "2026-09-09 20:06:06 | synced=2 blocked=0 skipped=0 errors=0 noop=0\n"
    )
    repos = parse_summary(text)["repos"]
    assert "sentinel_fork/agents/chat_agent" in repos
    assert "sonar/sonar/macro" in repos
    assert repos["sonar/sonar/macro"]["status"] == "SYNCED"


def test_push_rejection_reason_is_carried_into_the_summary():
    """'push failed' sent the user to a log; the cause belongs in the summary."""
    text = (
        "2026-10-05 23:20:11 | SUMMARY:\n"
        "2026-10-05 23:20:11 |    ERROR   sentinel_fork  (remote has 6 commit(s) "
        "you do not have - pull first)\n"
        "2026-10-05 23:20:11 | synced=0 blocked=0 skipped=0 errors=1 noop=0\n"
    )
    info = parse_summary(text)["repos"]["sentinel_fork"]
    assert info["status"] == "ERROR"
    assert "pull first" in info["detail"]


def test_behind_remote_is_reported_even_on_a_successful_push():
    text = (
        "2026-10-05 23:20:11 | SUMMARY:\n"
        "2026-10-05 23:20:11 |    SYNCED  lab_hub  (behind remote by 3 - run git pull)\n"
        "2026-10-05 23:20:11 | synced=1 blocked=0 skipped=0 errors=0 noop=0\n"
    )
    info = parse_summary(text)["repos"]["lab_hub"]
    assert info["status"] == "SYNCED"
    assert "behind remote by 3" in info["detail"]
