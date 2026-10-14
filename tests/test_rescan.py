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


def test_github_link_follows_the_name_and_clears():
    from app.repo_row import RepoRow
    url = "https://github.com/wwds-dev/proj"
    with_link = RepoRow._name_markup("a/proj", url)
    assert f"href='{url}'" in with_link
    assert with_link.index("<b>proj</b>") < with_link.index("href=") < with_link.index("a/")
    assert "href" not in RepoRow._name_markup("a/proj")


def test_row_link_is_set_cleared_and_survives_missing_toggle(qapp):
    from app.repo_row import RepoRow
    row = RepoRow("proj", lambda n: None, lambda n: None, on_privacy=lambda n: None)
    row.set_github_url("https://github.com/wwds-dev/proj")
    assert "href=" in row.label.text()
    row.set_missing(True)
    assert "href=" not in row.label.text()          # a gone repo shows plain text
    row.set_missing(False)
    assert "href=" in row.label.text()
    row.set_github_url(None)
    assert "href=" not in row.label.text()
