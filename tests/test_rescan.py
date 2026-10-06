"""Reconciling the repo list against what is on disk."""
from app.rescan_dialog import plan_changes, _relocate_candidate

DISCOVERED = [
    "backup_manager", "bazaar", "git_autosync", "imprint", "imprint/vidforge",
    "toolbox", "toolbox/convert_epub", "sentinel/vpn_agent",
]


def test_moved_repo_is_relocated_not_dropped(monkeypatch):
    """A reorg must not silently delete the entry — it moved, it didn't vanish."""
    monkeypatch.setattr("app.paths.repo_exists", lambda e: e in DISCOVERED)
    plan = plan_changes(["convert_epub", "vidforge"], DISCOVERED)
    assert plan["relocate"] == {
        "convert_epub": "toolbox/convert_epub",
        "vidforge": "imprint/vidforge",
    }
    assert plan["drop"] == []


def test_vanished_repo_is_dropped(monkeypatch):
    monkeypatch.setattr("app.paths.repo_exists", lambda e: e in DISCOVERED)
    plan = plan_changes(["sentinel_ai"], DISCOVERED)
    assert plan["drop"] == ["sentinel_ai"]
    assert plan["relocate"] == {}


def test_rename_is_not_guessed(monkeypatch):
    """create_and_publish -> imprint is a rename; no basename match, so drop it
    rather than inventing a relocation the user never asked for."""
    monkeypatch.setattr("app.paths.repo_exists", lambda e: e in DISCOVERED)
    plan = plan_changes(["create_and_publish"], DISCOVERED)
    assert plan["drop"] == ["create_and_publish"]
    assert "create_and_publish" not in plan["relocate"]


def test_new_repos_exclude_relocation_targets(monkeypatch):
    """A relocated entry must not also show up as 'new' — that would add a
    duplicate line for the same repo."""
    monkeypatch.setattr("app.paths.repo_exists", lambda e: e in DISCOVERED)
    plan = plan_changes(["convert_epub", "backup_manager"], DISCOVERED)
    assert "toolbox/convert_epub" not in plan["new"]
    assert "backup_manager" not in plan["new"]
    assert "bazaar" in plan["new"]


def test_ambiguous_basename_is_not_relocated():
    """Two candidates with the same basename: refuse to pick one."""
    assert _relocate_candidate("sports", ["a/sports", "b/sports"]) is None
    assert _relocate_candidate("sports", ["a/sports"]) == "a/sports"


def test_clean_list_plans_nothing(monkeypatch):
    monkeypatch.setattr("app.paths.repo_exists", lambda e: e in DISCOVERED)
    plan = plan_changes(DISCOVERED, DISCOVERED)
    assert plan == {"relocate": {}, "drop": [], "new": []}


def test_name_markup_puts_repo_name_first():
    """'sonar/sonar/macro' should read as macro, living in sonar/sonar/."""
    from app.repo_row import RepoRow
    out = RepoRow._name_markup("sonar/sonar/macro")
    assert out.index("<b>macro</b>") < out.index("sonar/sonar/")
    assert RepoRow._name_markup("bazaar") == "<b>bazaar</b>"
