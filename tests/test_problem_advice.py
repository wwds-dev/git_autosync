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
