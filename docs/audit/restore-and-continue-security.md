# Security review: feat/restore-and-continue (security-engineer), 2026-10-21

At 09ef3ed and lab root 402bafc. **No P0/P1.** Saved by the orchestrator.

P2 (fixed after, with tests): (1) the manifest scan read `./.gitleaksignore` from the last
repo's working tree (it ran from that cwd without `-i`), so a planted allowlist cleared a token
in the manifest — now an empty allowlist from the temp dir; (2) `restore.sh` wrote wherever the
manifest said (`..`, absolute paths: a repo cloned into `~/Library/LaunchAgents`) and passed the
URL without `--` — now only `~/Documents/` paths, GitHub https URLs, `--`, and valid branch
names. (3) App data in plaintext on Drive: accepted by the owner until item 4.

P3 fixed: userinfo stripped for every URL scheme and query strings dropped; the run-lock
takeover race (atomic pid, grace for a pid-less lock, release only if still ours); database
names with a quote (numbered staging names). Checked sound: `--backup-only` keeps the whole
gate; the manifest carries fingerprints only; no command injection via manifest fields.
