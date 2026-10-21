#!/bin/bash
# =============================================================================
#  restore.sh  —  put every git_autosync repo back on a new Mac
# -----------------------------------------------------------------------------
#  Reads the manifest git_autosync.sh pushes to the private backup repo
#  (refs/backup-meta/manifest) and, for every repo in it:
#     1. clones its origin to its old path (or `git init`s one with no origin);
#     2. recreates every local branch at its backed-up commit;
#     3. checks out the branch it was on and lays its uncommitted and untracked
#        files back over the work tree, as uncommitted changes;
#     4. turns each other worktree's work and each stash into a branch:
#        restored/wt/<id>, restored/stash/<sha>.
#  It also puts back git_autosync's repo list and allowlist, so autosync runs
#  again unchanged. It never writes into a folder that already holds files.
#
#  Before it, on the new Mac:   gh auth login && gh auth setup-git
#  Usage:
#     ./restore.sh                 # restore everything
#     ./restore.sh --dry-run       # say what it would do, change nothing
#     ./restore.sh --only ENTRY    # one repo (its config entry, e.g. sonar/sonar/macro)
#
#  docs/specs/restore-and-continue.md; README "Restoring".
# =============================================================================
set -uo pipefail

BACKUP_REMOTE="${AUTOSYNC_BACKUP_REMOTE:-https://github.com/wwds-dev/mac_backup.git}"
LAB_ACTIVE="${LAB_ACTIVE:-$HOME/Documents/lab/active}"
SUPPORT="${AUTOSYNC_SUPPORT_DIR:-$HOME/Library/Application Support/git_autosync}"
DRY_RUN=0
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --only) ONLY="${2:-}"; shift ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done

TMP="$(mktemp -d "${TMPDIR:-/tmp}/autosync_restore.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
say(){ echo "$*"; }
declare -a REPORT
N_OK=0; N_SKIP=0; N_FAIL=0

# The same encoding the engine uses for ref names (git_autosync.sh ref_key).
ref_key(){
  local s="$1" out="" c i n
  local LC_ALL=C
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [A-Za-z0-9_-]) out="$out$c" ;;
      *) printf -v n '%d' "'$c"; printf -v c '%%%02X' $(( (n + 256) % 256 )); out="$out$c" ;;
    esac
  done
  printf '%s' "$out"
}
expand(){ case "$1" in "~"*) printf '%s' "$HOME${1#\~}" ;; *) printf '%s' "$1" ;; esac; }

# --- the manifest ----------------------------------------------------------
META="$TMP/meta.git"
git init -q --bare "$META"
if ! git --git-dir="$META" fetch -q "$BACKUP_REMOTE" "+refs/backup-meta/manifest:refs/manifest" 2>"$TMP/err"; then
  say "ERROR: cannot read the manifest from $BACKUP_REMOTE"
  sed 's/^/  /' "$TMP/err"
  say "  (signed in?  gh auth login && gh auth setup-git)"
  exit 1
fi
git --git-dir="$META" show refs/manifest:manifest.tsv > "$TMP/manifest.tsv" || { say "ERROR: manifest has no manifest.tsv"; exit 1; }
say "Manifest: $(grep -c '^repo' "$TMP/manifest.tsv") repo(s), from $BACKUP_REMOTE"

# --- git_autosync's own state: repo list and allowlist ------------------------
# Only when absent: a list or allowlist already on this Mac is newer than any
# backup of it.
restore_state(){
  local f name
  if [ -e "$SUPPORT/autosync_repos.txt" ]; then
    say "  repo list: kept the one already at $SUPPORT"
  elif [ "$DRY_RUN" -eq 1 ]; then
    say "  repo list: would restore to $SUPPORT/autosync_repos.txt"
  else
    mkdir -p "$SUPPORT"
    git --git-dir="$META" show refs/manifest:autosync_repos.txt > "$SUPPORT/autosync_repos.txt"
    say "  repo list: restored to $SUPPORT/autosync_repos.txt"
  fi
  while IFS= read -r f; do
    name="${f#allowlist/}"
    if [ -e "$SUPPORT/gitleaksignore/$name" ]; then continue; fi
    [ "$DRY_RUN" -eq 1 ] && { say "  allowlist: would restore $name"; continue; }
    mkdir -p "$SUPPORT/gitleaksignore"
    git --git-dir="$META" show "refs/manifest:$f" > "$SUPPORT/gitleaksignore/$name"
    say "  allowlist: restored $name"
  done < <(git --git-dir="$META" ls-tree -r --name-only refs/manifest -- allowlist/ 2>/dev/null)
}
[ -z "$ONLY" ] && restore_state

# --- one repo ---------------------------------------------------------------
# restore_repo <entry> <path> <origin url> <checked-out branch>
restore_repo(){
  local entry="$1" dir url="$3" branch="$4" key ref sha b wip head n_br=0 n_extra=0
  dir="$(expand "$2")"; key="$(ref_key "$entry")"
  if [ -d "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    REPORT+=("SKIP  $entry  (already there: $2)"); N_SKIP=$((N_SKIP+1)); return
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    REPORT+=("WOULD $entry  -> $2  (${url:-no origin}; on ${branch:-detached HEAD})"); N_OK=$((N_OK+1)); return
  fi
  mkdir -p "$(dirname "$dir")"
  if [ -n "$url" ]; then
    git clone -q "$url" "$dir" 2>"$TMP/err" || { REPORT+=("FAIL  $entry  (clone failed: $(head -1 "$TMP/err"))"); N_FAIL=$((N_FAIL+1)); return; }
  else
    git init -q "$dir"
  fi
  # Into a private namespace first, then out again: nothing of the backup's
  # layout is left in the restored repo.
  if ! git -C "$dir" fetch -q "$BACKUP_REMOTE" "+refs/backup/$key/*:refs/restore/*" 2>"$TMP/err"; then
    REPORT+=("FAIL  $entry  (cloned, but its backup could not be fetched)"); N_FAIL=$((N_FAIL+1)); return
  fi
  while read -r sha ref; do
    b="${ref#refs/restore/heads/}"
    git -C "$dir" update-ref "refs/heads/$b" "$sha" && n_br=$((n_br+1))
    git -C "$dir" show-ref -q --verify "refs/remotes/origin/$b" \
      && git -C "$dir" branch -q --set-upstream-to="origin/$b" "$b" 2>/dev/null
  done < <(git -C "$dir" for-each-ref --format='%(objectname) %(refname)' refs/restore/heads/)

  # The main worktree: the branch it was on, then its uncommitted work laid
  # over the files as uncommitted changes (a clone has no local changes, so
  # the forced checkout loses nothing).
  wip="$(git -C "$dir" rev-parse -q --verify refs/restore/wip/main 2>/dev/null)"
  if [ -n "$branch" ] && git -C "$dir" show-ref -q --verify "refs/heads/$branch"; then
    git -C "$dir" checkout -q -f "$branch"
  elif [ -n "$wip" ]; then
    head="$(git -C "$dir" rev-parse -q --verify "$wip^" 2>/dev/null || echo "$wip")"
    git -C "$dir" checkout -q -f --detach "$head"
  fi
  if [ -n "$wip" ] && [ "$wip" != "$(git -C "$dir" rev-parse -q --verify HEAD)" ]; then
    git -C "$dir" restore --source="$wip" --worktree -- . 2>/dev/null \
      || git -C "$dir" checkout "$wip" -- . 2>/dev/null
  fi

  # Other worktrees and stashes come back as branches (owner, 2026-10-21).
  while read -r sha ref; do
    b="restored/${ref#refs/restore/wip/}"
    git -C "$dir" update-ref "refs/heads/$b" "$sha" && n_extra=$((n_extra+1))
  done < <(git -C "$dir" for-each-ref --format='%(objectname) %(refname)' refs/restore/wip/wt/)
  while read -r sha ref; do
    b="restored/stash/$(printf '%s' "${ref#refs/restore/stash/}" | cut -c1-12)"
    git -C "$dir" update-ref "refs/heads/$b" "$sha" && n_extra=$((n_extra+1))
  done < <(git -C "$dir" for-each-ref --format='%(objectname) %(refname)' refs/restore/stash/)

  git -C "$dir" for-each-ref --format='delete %(refname)' refs/restore/ | git -C "$dir" update-ref --stdin
  REPORT+=("OK    $entry  -> $2  ($n_br branch(es), $n_extra worktree/stash branch(es); on ${branch:-detached HEAD})")
  N_OK=$((N_OK+1))
}

# Parents before nested repos (toolbox before toolbox/convert_epub), so a
# nested repo's folder is created inside its parent's clone, not before it.
while IFS=$'\t' read -r kind entry mode path url branch; do
  [ "$kind" = "repo" ] || continue
  [ -n "$ONLY" ] && [ "$entry" != "$ONLY" ] && continue
  [ "$url" = "-" ] && url=""; [ "$branch" = "-" ] && branch=""
  restore_repo "$entry" "$path" "$url" "$branch"
done < <(awk -F'\t' '$1 == "repo" { print length($4) "\t" $0 }' "$TMP/manifest.tsv" | sort -n | cut -f2-)

say ""
for r in "${REPORT[@]:-}"; do [ -n "$r" ] && say "  $r"; done
say ""
say "restored=$N_OK skipped=$N_SKIP failed=$N_FAIL"
[ "$N_FAIL" -eq 0 ]
