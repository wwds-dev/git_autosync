"""A failing run must explain itself and point at the fix."""
from app.problem_advice import advise, collect


def test_each_repo_gets_its_own_decision():
    """Two blocked repos can need opposite answers — one a false positive, one
    a real secret — so each must carry its own repo and its own action."""
    advices = collect({
        "imprint":       {"status": "BLOCKED", "detail": "secret in changes - see log"},
        "sentinel_fork": {"status": "BLOCKED", "detail": "secret in history - see log"},
    })
    assert [a.repo for a in advices] == ["imprint", "sentinel_fork"]
    assert all(a.action == "leak" for a in advices)
    assert len({a.repo for a in advices}) == 2


def test_clean_repos_produce_no_advice():
    assert advise("sonar", "SYNCED", "") is None
    assert advise("sonar", "OK", "dry-run: nothing to commit") is None
    assert collect({"sonar": {"status": "SYNCED", "detail": ""}}) == []


def test_behind_remote_offers_pull():
    a = advise("wifi_agent", "ERROR", "behind remote by 4 - pull first")
    assert a.action == "pull"
    assert "behind" in a.headline or "do not have" in a.headline


def test_missing_remote_offers_repo_creation():
    a = advise("headroom", "SKIP", "no GitHub remote")
    assert a.action == "create_repo"


def test_unactionable_failures_still_explain_themselves():
    """No button, but never a bare status — auth and network need a human."""
    for detail in ("not authorised to push - check gh auth / credentials",
                   "network problem reaching GitHub - transient, try again"):
        a = advise("backup_manager", "ERROR", detail)
        assert a.action is None
        assert len(a.explanation) > 40


def test_worst_problems_come_first():
    advices = collect({
        "a": {"status": "SKIP", "detail": "no GitHub remote"},
        "b": {"status": "ERROR", "detail": "behind remote by 1 - pull first"},
        "c": {"status": "BLOCKED", "detail": "secret"},
    })
    assert [a.repo for a in advices] == ["c", "b", "a"]


# ── Leak triage wording ───────────────────────────────────────────

def _plain(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ")


FINDING = {"file": "services/database.py", "line": 198,
           "rule": "generic-api-key", "fingerprint": "abc:f:generic-api-key:198"}


def test_triage_branches_read_as_choices_not_contradictions():
    """'• No — …' followed by '• Yes: …' read as two opposing statements about
    the same finding. Each branch must be conditional and name its button."""
    from app import leak_triage as lt

    for where in (lt.UNCOMMITTED, lt.LOCAL, "published"):
        text = _plain(lt.describe_block("imprint", FINDING, where))
        assert "If it is NOT" in text, where
        assert "If it IS" in text, where
        assert "• No —" not in text and "• Yes:" not in text, where
        assert "Allowlist (false positive)" in text, where


def test_published_branch_puts_rotation_before_history_rewriting():
    """Cleaning history does not make a leaked key safe, so rotation has to
    come first and be unmistakable."""
    from app import leak_triage as lt

    text = _plain(lt.describe_block("imprint", FINDING, "published"))
    assert text.index("Rotate or revoke") < text.index("Remove from history")


def test_uncommitted_branch_offers_no_history_button():
    """Nothing was committed, so there is no history to rewrite — the text must
    not send the user to a button the dialog does not even show."""
    from app import leak_triage as lt

    text = _plain(lt.describe_block("imprint", FINDING, lt.UNCOMMITTED))
    assert "Remove from history" not in text


# ── Allowlist entries must be readable ────────────────────────────

def test_fingerprint_splits_into_judgeable_parts():
    """A fingerprint shown whole is truncated mid-hash, hiding the file — the
    one part that lets a user decide whether the entry is safe to keep."""
    from app.leak_triage import parse_fingerprint

    p = parse_fingerprint(
        "9e85a0328e31013b92df17442b2a9b7d7733924e:tests/test_vpn_execution.py:"
        "generic-api-key:17")
    assert p["file"] == "tests/test_vpn_execution.py"
    assert p["line"] == "17"
    assert p["rule"] == "generic-api-key"
    assert p["commit"].startswith("9e85a03")


def test_path_containing_a_colon_still_splits_correctly():
    """Split from the right: a path may contain a colon, the trailing three
    fields never do."""
    from app.leak_triage import parse_fingerprint

    p = parse_fingerprint("abc123:weird:name/file.py:generic-api-key:9")
    assert p["file"] == "weird:name/file.py"
    assert p["line"] == "9"
    assert p["rule"] == "generic-api-key"


def test_malformed_entry_is_shown_rather_than_dropped():
    """An unparseable line is still a line in the file; hiding it would make
    the dialog disagree with .gitleaksignore."""
    from app.leak_triage import parse_fingerprint

    p = parse_fingerprint("not-a-fingerprint")
    assert p["file"] == "not-a-fingerprint"
    assert p["raw"] == "not-a-fingerprint"
