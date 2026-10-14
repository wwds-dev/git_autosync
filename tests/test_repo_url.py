import subprocess

from app import paths


def _repo(tmp_path, url):
    r = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    if url:
        subprocess.run(["git", "-C", str(r), "remote", "add", "origin", url], check=True)
    return r


def test_url_from_https_and_ssh_remotes(tmp_path):
    for url in ("https://github.com/wwds-dev/proj.git", "git@github.com:wwds-dev/proj.git"):
        r = _repo(tmp_path / url.replace("/", "_").replace(":", "_"), url)
        assert paths.repo_github_url(str(r)) == "https://github.com/wwds-dev/proj"


def test_no_link_without_a_github_remote(tmp_path):
    assert paths.repo_github_url(str(_repo(tmp_path / "a", None))) is None
    assert paths.repo_github_url(str(_repo(tmp_path / "b", "https://gitlab.com/x/y.git"))) is None
