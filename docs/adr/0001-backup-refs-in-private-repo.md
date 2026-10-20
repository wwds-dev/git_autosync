# ADR 0001: backup refs live in one private repo, not in each repo's origin

Date: 2026-10-20 · Status: **accepted by the owner** · Spec: `docs/specs/full-mac-backup.md`

## Context
git_autosync must back up local-only branches and uncommitted work without creating
visible branches (owner decision, 2026-10-20). Git allows any ref namespace, so the obvious
place is `refs/backup/*` on each repo's own `origin`. But a hidden ref is only hidden from
the GitHub UI: on a public repo anyone can `git ls-remote` and fetch it. sentinel, sonar,
imprint and lab_hub are public and hold most of the gaps.

## Decision
All backup refs are pushed to one private repo, `wwds-dev/mac_backup`
(`AUTOSYNC_BACKUP_REMOTE`), under `refs/backup/<entry>/` where `<entry>` is the config entry
with `%` and `/` percent-encoded. Inside it: `heads/<branch>`, `wip/main`,
`wip/wt/<worktree>`, `stash/<sha>`. The push goes to the URL directly, so no remote is added
to any repo's config.

## Consequences
- Nothing unfinished is ever published, whatever a repo's visibility is now or later.
- One place to restore from; the README gives the fetch command.
- The first run uploads each repo's history once (~75 MB across all repos on 2026-10-20).
- Restoring means fetching from a second URL, not from `origin`.
- Reversing it later means re-pushing every backup ref somewhere else; the refs are
  disposable copies, so that is cheap, but restore instructions would change.
