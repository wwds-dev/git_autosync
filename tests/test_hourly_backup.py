"""The hourly backup agent: its own label, --backup-only, every hour."""
import plistlib
from pathlib import Path

from app import scheduler


def test_the_hourly_agent_runs_backup_only_every_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    scheduler.install_hourly_backup(tmp_path / "repos.txt")
    data = plistlib.loads(scheduler.hourly_plist_path().read_bytes())
    assert data["Label"] == "com.wwds-dev.git-autosync-hourly"
    assert data["ProgramArguments"][-1] == "--backup-only"
    assert data["StartInterval"] == 3600
    assert data["EnvironmentVariables"]["AUTOSYNC_CONFIG"] == str(tmp_path / "repos.txt")
    assert not scheduler.is_installed(), "the nightly agent is a separate file"
    scheduler.uninstall_hourly_backup()
    assert not scheduler.is_hourly_backup_installed()
    assert any("load" in c for c in calls)
