"""Last-synced lookups, including the pre-2026-09 basename keys."""
from datetime import datetime, timedelta

from app import repo_state


def _stamp(**delta) -> str:
    """A timestamp relative to now — a fixed date would rot as the clock moves."""
    return (datetime.now() - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


def test_entry_key_is_preferred(monkeypatch):
    monkeypatch.setattr(repo_state, "read_all",
                        lambda: {"sentinel/vpn_agent": _stamp(minutes=5),
                                 "vpn_agent": _stamp(days=900)})
    assert repo_state.days_since_synced("sentinel/vpn_agent") == 0
    assert repo_state.time_since_synced("sentinel/vpn_agent") == "5m ago"


def test_falls_back_to_basename_for_old_state(monkeypatch):
    """State written before the engine reported config entries."""
    monkeypatch.setattr(repo_state, "read_all", lambda: {"vpn_agent": _stamp(hours=2)})
    assert repo_state.time_since_synced("sentinel/vpn_agent") == "2h ago"


def test_unknown_repo_is_never(monkeypatch):
    monkeypatch.setattr(repo_state, "read_all", lambda: {"other": _stamp(minutes=1)})
    assert repo_state.time_since_synced("sentinel/vpn_agent") is None
    assert repo_state.is_stale("sentinel/vpn_agent") is True
