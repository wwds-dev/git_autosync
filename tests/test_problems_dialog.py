"""The problems dialog runs several fixes at once and stays open, so fixing
one repo no longer means rescanning every repo to see the rest."""
import time

from PySide6.QtWidgets import QApplication

from app.problem_advice import advise
from app.problems_dialog import ProblemsDialog


def _advices():
    return [
        advise("leaky", "BLOCKED", "secret in changes"),
        advise("alpha", "ERROR", "behind remote by 2 - pull first"),
        advise("beta", "ERROR", "behind remote by 1 - pull first"),
        advise("gone", "SKIP", "not a git repo"),
    ]


def _wait(dialog, timeout=5.0):
    end = time.monotonic() + timeout
    while dialog._thread is not None and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert dialog._thread is None, "batch did not finish"


def _make(fix_one=None, **kw):
    calls = {"fix": [], "after": [], "sync": [], "action": []}

    def fix(action, repo):
        calls["fix"].append((action, repo))
        return fix_one(action, repo) if fix_one else (True, "ok")

    d = ProblemsDialog(None, _advices(),
                       lambda a, r: calls["action"].append((a, r)),
                       fix_one=fix,
                       after_fixes=calls["after"].append,
                       sync_repos=calls["sync"].append, **kw)
    return d, calls


def test_only_fixes_without_a_window_of_their_own_get_a_checkbox():
    assert advise("a", "ERROR", "behind remote by 1 - pull first").batchable
    assert advise("a", "SKIP", "not a git repo").batchable
    assert not advise("a", "BLOCKED", "secret").batchable
    assert not advise("a", "SKIP", "no GitHub remote").batchable

    d, _ = _make()
    checks = {c.adv.repo: c.check for c in d._cards}
    assert checks["leaky"] is None
    assert all(checks[n].isChecked() for n in ("alpha", "beta", "gone"))
    assert d._fix_btn.text() == "Fix selected (3)"


def test_batch_reports_each_result_and_keeps_the_dialog_open():
    d, calls = _make(lambda a, r: (False, "diverged (1 local commit(s))")
                     if r == "beta" else (True, "up to date"))
    d.show()
    by = {c.adv.repo: c for c in d._cards}
    by["gone"].check.setChecked(False)          # an unticked fix is not run

    d.start_batch([c for c in d._cards if c.check and c.check.isChecked()])
    _wait(d)

    assert sorted(calls["fix"]) == [("pull", "alpha"), ("pull", "beta")]
    assert len(calls["after"]) == 1 and len(calls["after"][0]) == 2
    assert d.isVisible()
    assert by["alpha"].done and "✓" in by["alpha"].status.text()
    assert not by["beta"].done and "diverged" in by["beta"].status.text()
    assert by["beta"].button.text() == "Resolve…"
    assert not by["beta"].check.isChecked()       # would only fail again
    assert not by["gone"].done
    assert d.fixed_repos == ["alpha"]
    assert d._head.text() == "3 of 4 still need attention."


def test_sync_fixed_syncs_only_the_fixed_repos():
    d, calls = _make()
    d.start_batch([c for c in d._cards if c.check])
    _wait(d)
    assert d._sync_btn.text() == "Sync fixed repos (2)"   # removed one is not synced

    d._on_sync_fixed()

    assert calls["sync"] == [["alpha", "beta"]]
    assert d.result() == ProblemsDialog.Accepted


def test_closing_is_refused_while_a_batch_runs():
    slow = lambda a, r: (time.sleep(0.2), (True, "ok"))[1]
    d, _ = _make(slow)
    d.show()
    d.start_batch([d._cards[1]])
    d.reject()
    assert d.isVisible()
    _wait(d)
    d.reject()
    assert not d.isVisible()


def test_interactive_fix_hides_then_returns_with_the_card_updated():
    d, calls = _make(resolved=lambda a, r: None)
    d.show()
    d._on_single(0)                                     # leak triage
    assert calls["action"] == [("leak", "leaky")]
    assert d.isVisible()
    assert not d._cards[0].done                         # cannot tell yet
    assert "leaky" in d.fixed_repos                     # but offered for re-sync


def test_interactive_fix_that_starts_a_run_closes_the_dialog():
    d, _ = _make(busy=lambda: True)
    d.show()
    d._on_single(1)                                     # Pull → rebase and sync
    assert not d.isVisible()
    assert d.result() == ProblemsDialog.Accepted
