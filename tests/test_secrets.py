"""The secrets bundle and restore-secrets.sh (backup_manager/docs/specs/secrets-vault.md).

The bundle script lives in the lab root (_Admin/backup/backup_secrets.sh) and
runs here against a fake home folder, so no real key is ever read.
"""
from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RESTORE = ROOT / "restore-secrets.sh"
BUNDLER = Path.home() / "Documents" / "lab" / "_Admin" / "backup" / "backup_secrets.sh"
pytestmark = pytest.mark.skipif(not BUNDLER.exists(), reason="lab root bundle script not here")


@pytest.fixture
def fake_mac(tmp_path):
    home = tmp_path / "home"
    proj = home / "Documents" / "lab" / "active" / "proj"
    proj.mkdir(parents=True)
    (proj / ".env").write_text("API_KEY=dummy-not-a-real-key\n")
    (proj / ".env.local").write_text("DB_PASSWORD=dummy\n")
    (proj / ".env.example").write_text("API_KEY=\n")          # no values: not bundled
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / ".env").write_text("x\n")         # dependency trees: not bundled
    app = home / "Library" / "Application Support" / "Imprint"
    app.mkdir(parents=True)
    (app / "settings.json").write_text('{"key": "dummy"}\n')
    subprocess.run(["sqlite3", str(app / "imprint.db"),
                    "create table t(x); insert into t values (42);"], check=True)
    vault = tmp_path / "proton"
    vault.mkdir()
    return tmp_path, home, vault


def bundle(root, home, vault, *extra_env):
    env = {**os.environ, "HOME": str(home), "PROTON_ROOT": str(vault), "LOG": str(root / "log")}
    env.update(dict(extra_env))
    return subprocess.run(["bash", str(BUNDLER)], capture_output=True, text=True, env=env)


def test_the_bundle_holds_the_secrets_and_nothing_else(fake_mac):
    root, home, vault = fake_mac
    r = bundle(root, home, vault)
    assert r.returncode == 0, r.stderr
    latest = vault / "Vault" / "Credentials-Recovery" / "Mac-secrets" / "latest.tar.gz"
    names = tarfile.open(latest).getnames()
    assert "home/Documents/lab/active/proj/.env" in names
    assert "home/Documents/lab/active/proj/.env.local" in names
    assert "home/Library/Application Support/Imprint/imprint.db" in names
    assert not any(n.endswith(".env.example") or "node_modules" in n for n in names)
    manifest = tarfile.open(latest).extractfile("MANIFEST.tsv").read().decode()
    assert manifest.count("\n") == 4


def test_no_vault_mounted_writes_nothing_and_fails(fake_mac):
    root, home, vault = fake_mac
    r = bundle(root, home, vault, ("PROTON_ROOT", str(root / "not-mounted")))
    assert r.returncode != 0 and "not mounted" in r.stderr


def test_only_the_last_seven_dated_bundles_are_kept(fake_mac):
    root, home, vault = fake_mac
    dest = vault / "Vault" / "Credentials-Recovery" / "Mac-secrets"
    dest.mkdir(parents=True)
    for d in range(1, 10):
        (dest / f"secrets_2026-01-0{d}.tar.gz").write_text("old")
    bundle(root, home, vault)
    assert len(list(dest.glob("secrets_*.tar.gz"))) == 7


def test_restore_puts_every_file_back_byte_for_byte(fake_mac):
    root, home, vault = fake_mac
    bundle(root, home, vault)
    latest = vault / "Vault" / "Credentials-Recovery" / "Mac-secrets" / "latest.tar.gz"
    new = root / "newmac"
    r = subprocess.run(["bash", str(RESTORE), str(latest), "--into", str(new)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "restored=4" in r.stdout, r.stdout
    env = new / "Documents" / "lab" / "active" / "proj" / ".env"
    assert env.read_text() == "API_KEY=dummy-not-a-real-key\n"
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    db = new / "Library" / "Application Support" / "Imprint" / "imprint.db"
    assert subprocess.run(["sqlite3", str(db), "select x from t"], capture_output=True,
                          text=True).stdout.strip() == "42"


def test_restore_keeps_a_newer_file(fake_mac):
    root, home, vault = fake_mac
    bundle(root, home, vault)
    latest = vault / "Vault" / "Credentials-Recovery" / "Mac-secrets" / "latest.tar.gz"
    new = root / "newmac"
    target = new / "Documents" / "lab" / "active" / "proj" / ".env"
    target.parent.mkdir(parents=True)
    target.write_text("API_KEY=rotated\n")
    os.utime(target, (4102444800, 4102444800))   # 2100: certainly newer
    r = subprocess.run(["bash", str(RESTORE), str(latest), "--into", str(new)],
                       capture_output=True, text=True)
    assert "KEPT" in r.stdout and target.read_text() == "API_KEY=rotated\n"


def test_a_crafted_bundle_cannot_write_outside_home(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as t:
        for name, data in (("MANIFEST.tsv", b"~/../../tmp/pwn\t1\tx\n"), ("home/../../pwn", b"x")):
            info = tarfile.TarInfo(name); info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    r = subprocess.run(["bash", str(RESTORE), str(evil), "--into", str(tmp_path / "into")],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "refusing" in r.stdout
    assert not (tmp_path / "pwn").exists()


def test_more_env_spellings_are_bundled_not_left_for_google(fake_mac):
    root, home, vault = fake_mac
    proj = home / "Documents" / "lab" / "active" / "proj"
    for n in (".envrc", "env.local", "config.env.local", "secrets.env.bak", ".env-prod"):
        (proj / n).write_text("X=dummy\n")
    (proj / "env.py").write_text("x = 1\n")
    bundle(root, home, vault)
    names = tarfile.open(vault / "Vault" / "Credentials-Recovery" / "Mac-secrets" / "latest.tar.gz").getnames()
    for n in (".envrc", "env.local", "config.env.local", "secrets.env.bak", ".env-prod"):
        assert f"home/Documents/lab/active/proj/{n}" in names, n
    assert "home/Documents/lab/active/proj/env.py" not in names


def test_a_symlinked_vault_or_a_google_folder_is_refused(fake_mac):
    root, home, vault = fake_mac
    link = root / "link"
    link.symlink_to(vault)
    assert bundle(root, home, vault, ("PROTON_ROOT", str(link))).returncode != 0
    g = root / "CloudStorage" / "GoogleDrive-x"
    g.mkdir(parents=True)
    r = bundle(root, home, vault, ("PROTON_ROOT", str(g)))
    assert r.returncode != 0 and "not a Proton folder" in r.stderr


def test_restore_refuses_paths_that_run_at_login_and_link_entries(tmp_path):
    for name, kind in (("home/.zshrc", "file"), ("home/Documents/x", "link")):
        evil = tmp_path / f"evil_{kind}.tar.gz"
        with tarfile.open(evil, "w:gz") as t:
            rel = name[len("home/"):]
            m = f"~/{rel}\t1\tx\n".encode()
            info = tarfile.TarInfo("MANIFEST.tsv"); info.size = len(m); t.addfile(info, io.BytesIO(m))
            info = tarfile.TarInfo(name)
            if kind == "link":
                info.type = tarfile.SYMTYPE; info.linkname = "/etc/hosts"; t.addfile(info)
            else:
                info.size = 1; t.addfile(info, io.BytesIO(b"x"))
        r = subprocess.run(["bash", str(RESTORE), str(evil), "--into", str(tmp_path / "into")],
                           capture_output=True, text=True)
        assert r.returncode != 0 and ("REFUSED" in r.stdout or "refusing" in r.stdout), r.stdout
        assert not (tmp_path / "into" / rel).exists()
