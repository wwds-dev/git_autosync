"""The GUI half of full-mac-backup: a row says "Backed up" only when it is."""
from app.problem_advice import collect
from app.runner import parse_summary
from app.ui_main import _with_backup

OUTPUT = """\
2026-10-20 20:40:00 | REPO: sentinel  (/x/sentinel)
2026-10-20 20:40:01 | SUMMARY:
2026-10-20 20:40:01 |    BACKUP  sentinel  (ok: 3 branch(es), 2 worktree snapshot(s), 0 stash(es))
2026-10-20 20:40:01 |    OK      sentinel  (nothing to push)
2026-10-20 20:40:01 |    BACKUP  sonar  (failed: cannot reach the backup repo - see log)
2026-10-20 20:40:01 |    SYNCED  sonar
2026-10-20 20:40:01 |    BACKUP  imprint  (missing: 4 ref(s) not backed up yet)
2026-10-20 20:40:01 |    OK      imprint  (dry-run: nothing to push)
2026-10-20 20:40:01 |    UNLISTED ~/Documents/Websites/shopfront  (not in the repo list; no GitHub remote)
2026-10-20 20:40:01 | synced=1 blocked=0 skipped=0 errors=1 noop=2 unlisted=1
"""


def test_backup_and_unlisted_lines_are_parsed_without_clobbering_status():
    s = parse_summary(OUTPUT)
    assert s["repos"]["sentinel"]["status"] == "OK"
    assert s["repos"]["sonar"]["status"] == "SYNCED"
    assert s["backup"]["sentinel"]["state"] == "ok"
    assert s["backup"]["sonar"] == {"state": "failed",
                                    "detail": "cannot reach the backup repo - see log"}
    assert s["unlisted"] == [{"path": "~/Documents/Websites/shopfront",
                              "detail": "not in the repo list; no GitHub remote"}]
    assert s["counts"]["unlisted"] == 1


def test_counts_from_an_older_engine_still_parse():
    s = parse_summary("SUMMARY:\n| synced=1 blocked=0 skipped=0 errors=0 noop=0\n")
    assert s["counts"]["unlisted"] == 0


def test_row_status_follows_the_backup():
    s = parse_summary(OUTPUT)
    assert _with_backup("OK", "", s["backup"]["sentinel"])[0] == "BACKEDUP"
    assert _with_backup("SYNCED", "", s["backup"]["sonar"])[0] == "NOBACKUP"
    assert _with_backup("OK", "dry-run", s["backup"]["imprint"])[0] == "PENDING"
    # A blocked repo's own problem comes first.
    assert _with_backup("BLOCKED", "secret", s["backup"]["sonar"])[0] == "BLOCKED"
    # No BACKUP line: the row is what the engine said, never a claim of backup.
    assert _with_backup("OK", "", None) == ("OK", "")


def test_failed_backups_and_unlisted_repos_get_advice():
    s = parse_summary(OUTPUT)
    advice = collect(s["repos"], s["backup"], s["unlisted"])
    heads = [a.headline for a in advice]
    assert any(h.startswith("sonar — not backed up") for h in heads)
    assert any("Websites/shopfront" in h and "nothing backs it up" in h for h in heads)
    assert not any(h.startswith("sentinel") for h in heads)
