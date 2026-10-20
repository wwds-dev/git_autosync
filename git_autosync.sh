#!/bin/bash
# =============================================================================
#  git_autosync.sh  —  leak-gated auto commit & push for your GitHub projects
# -----------------------------------------------------------------------------
#  For each repo listed in autosync_repos.txt it will:
#     1. stage all changes (respecting .gitignore)
#     2. SCAN with gitleaks  ->  if any secret is found, that repo is BLOCKED
#        (nothing is committed or pushed) and the run is flagged.
#     3. if clean: commit (auto message) and push to GitHub.
#
#  Usage:
#     ./git_autosync.sh              # sync everything in the config
#     ./git_autosync.sh --dry-run    # scan + report only, never commit/push
#     ./git_autosync.sh --repo NAME  # just one repo from the config
#     ./git_autosync.sh --repo NAME --create-remote public|private
#                                     # for a repo with no GitHub remote yet:
#                                     # gate it first, then create the GitHub
#                                     # repo and push, instead of skipping.
#
#  Config file:  autosync_repos.txt  (one repo per line; name, ~path or /path)
#  Logs:         logs/autosync_YYYYMMDD.log
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${AUTOSYNC_CONFIG:-$SCRIPT_DIR/autosync_repos.txt}"
LOG_DIR="${AUTOSYNC_LOG_DIR:-$SCRIPT_DIR/logs}"
STATE_DIR="${AUTOSYNC_STATE_DIR:-$SCRIPT_DIR}"
LAB_ACTIVE="${LAB_ACTIVE:-$HOME/Documents/lab/active}"
GITLEAKS="${GITLEAKS_CMD:-gitleaks}"
GH="${GH_CMD:-gh}"
DRY_RUN=0
ONLY=""
CREATE_REMOTE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --repo)    ONLY="${2:-}"; shift ;;
    --create-remote) CREATE_REMOTE="${2:-}"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done

if [ -n "$CREATE_REMOTE" ]; then
  case "$CREATE_REMOTE" in
    public|private) ;;
    *) echo "--create-remote must be 'public' or 'private', got: $CREATE_REMOTE"; exit 2 ;;
  esac
  if [ -z "$ONLY" ]; then
    echo "--create-remote requires --repo NAME (only create one remote at a time)"; exit 2
  fi
fi

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/autosync_$(date +%Y%m%d).log"
ts(){ date '+%Y-%m-%d %H:%M:%S'; }
log(){ echo "$(ts) | $*" | tee -a "$LOG"; }
rule(){ echo "---------------------------------------------" | tee -a "$LOG"; }

log "=== git_autosync start (dry-run=$DRY_RUN) ==="

# --- preflight -------------------------------------------------------------
if ! command -v "$GITLEAKS" >/dev/null 2>&1; then
  log "ERROR: gitleaks not found on PATH."
  log "       Install it once with:  brew install gitleaks"
  exit 1
fi
log "gitleaks: $("$GITLEAKS" version 2>/dev/null | head -1)"

if [ -n "$CREATE_REMOTE" ] && ! command -v "$GH" >/dev/null 2>&1; then
  log "ERROR: gh (GitHub CLI) not found on PATH. Needed for --create-remote."
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  log "ERROR: config file not found: $CONFIG"
  exit 1
fi

resolve_repo(){
  local p="$1"
  case "$p" in
    /*) printf '%s' "$p" ;;
    "~"*) printf '%s' "${p/#\~/$HOME}" ;;
    *)  printf '%s' "$LAB_ACTIVE/$p" ;;
  esac
}

BK_TMP="$(mktemp -d "${TMPDIR:-/tmp}/autosync_backup.XXXXXX")"
trap 'rm -rf "$BK_TMP"' EXIT

# --- the gate ----------------------------------------------------------------
# Run a gitleaks scan. Returns: 0 = clean, 1 = LEAK found, 2 = tool error.
# --verbose prints File:/RuleID:/Line: per finding (secret value still
# redacted) so the GUI can show *what* tripped the gate without opening the
# log file. Output also reaches stdout, which is what the GUI parses.
#
# gitleaks exits 0 when its own `git log` fails — it logs "[git] fatal",
# scans 0 commits and reports "no leaks found" (8.30.1, verified 2026-10-20
# with one broken ref). So its exit code alone cannot say "clean": any error
# line of its own turns the scan into a failure. Fail closed.
gl_run(){
  local out="$BK_TMP/gl_out" rc
  "$GITLEAKS" "$@" > "$out" 2>&1; rc=$?
  tee -a "$LOG" < "$out"
  if [ $rc -eq 0 ] && tr -d '\033' < "$out" \
       | grep -Eq '(\[[0-9;]*m| )ERR(\[| )|\[git\] fatal|stderr is not empty'; then
    log "  gitleaks reported an error of its own — the scan did not complete"
    rc=2
  fi
  return $rc
}

# The scanner's rules come from the repo's *committed* .gitleaks.toml, never
# from the working tree: an untracked .gitleaks.toml with a `paths` allowlist
# switched the whole gate off (security review P1-3). .gitleaksignore still
# comes from the working tree, on purpose: it holds exact fingerprints, not
# patterns, and the Allowlist button must take effect before it is committed.
GL_CFG="$BK_TMP/gitleaks.toml"
gl_config(){
  local dir="$1"
  git -C "$dir" show HEAD:.gitleaks.toml > "$GL_CFG" 2>/dev/null \
    || printf '[extend]\nuseDefault = true\n' > "$GL_CFG"
}

# Merges are scanned as remerge diffs: a secret typed only while resolving a
# conflict is in no other commit (security review P2-1).
GL_LOG_OPTS="--full-history --diff-merges=remerge"

scan_staged(){
  local dir="$1" rc
  gl_config "$dir"
  gl_run git --staged --no-banner --redact --verbose --config "$GL_CFG" "$dir"; rc=$?
  if [ $rc -ge 2 ]; then   # older gitleaks without the `git` subcommand
    gl_run protect --staged --no-banner --redact --verbose --config "$GL_CFG" --source "$dir"; rc=$?
  fi
  return $rc
}
scan_history(){
  local dir="$1" rc
  # A ref git cannot read is a ref gitleaks silently did not scan.
  if ! git -C "$dir" rev-list --all --quiet >/dev/null 2>&1; then
    log "  git cannot read every ref and object here (git rev-list --all failed)"
    return 2
  fi
  gl_config "$dir"
  gl_run git --no-banner --redact --verbose --config "$GL_CFG" \
    --log-opts="--all $GL_LOG_OPTS" "$dir"; rc=$?
  if [ $rc -ge 2 ]; then
    gl_run detect --no-banner --redact --verbose --config "$GL_CFG" \
      --log-opts="--all $GL_LOG_OPTS" --source "$dir"; rc=$?
  fi
  return $rc
}

# --- backup refs -------------------------------------------------------------
# Everything on this Mac that `origin` does not have — side branches, and the
# uncommitted work in every worktree — is pushed to hidden refs in one private
# repo (docs/adr/0001). Hidden is not private: on a public repo anyone can
# fetch refs/backup/*, so they never go to origin. Set AUTOSYNC_BACKUP_REMOTE
# to an empty string to switch backups off.
#
#   refs/backup/<entry>/heads/<branch>   every local branch, force-updated
#   refs/backup/<entry>/wip/main         the main worktree: its uncommitted
#                                        work as a commit on HEAD, or HEAD
#   refs/backup/<entry>/wip/wt/<id>      each linked worktree, the same
#   refs/backup/<entry>/stash/<sha>      each stash entry, flattened
#
# A clean worktree's wip ref points at its HEAD rather than being dropped, so
# commits on a detached HEAD are backed up and an old snapshot can never be
# restored over work committed since.
#
# Nothing here writes to the repo's working tree, index, HEAD, branches,
# stash or config: snapshots are built in a temporary index and pushed by
# SHA to a URL, not to a configured remote.
BACKUP_REMOTE="${AUTOSYNC_BACKUP_REMOTE-https://github.com/wwds-dev/mac_backup.git}"
BACKUP_MAX_BYTES="${AUTOSYNC_BACKUP_MAX_BYTES:-52428800}"   # GitHub rejects >100 MB
# Commits whose author or committer is anyone else are not pushed to the
# backup: the owner's real address must never reach GitHub (owner, 2026-10-20:
# the wwds-dev noreply address and Claude's noreply address).
BACKUP_EMAILS="${AUTOSYNC_ALLOWED_EMAILS:-${AUTOSYNC_GIT_EMAIL:-243015673+wwds-dev@users.noreply.github.com} noreply@anthropic.com}"

# Any string as one ref component: every byte outside [A-Za-z0-9_-] becomes
# %XX ("sonar/sonar/macro" -> "sonar%2Fsonar%2Fmacro", "~" -> "%7E"). One
# component, so no entry can be read as a branch of another; and always a
# valid ref name, whatever the entry or worktree is called.
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

# Snapshot commits must be the same SHA when nothing changed, or every run
# would re-push every worktree. So: fixed identity, fixed message, and the
# dates of the commit they sit on (epoch 0 on an unborn branch).
bk_commit(){   # bk_commit <tree> <parent or ""> <message>
  local when="@0 +0000"
  [ -n "$2" ] && when="$(git log -1 --format='@%ct +0000' "$2")"
  GIT_AUTHOR_NAME="${AUTOSYNC_GIT_NAME:-wwds-dev}" \
  GIT_AUTHOR_EMAIL="${AUTOSYNC_GIT_EMAIL:-243015673+wwds-dev@users.noreply.github.com}" \
  GIT_COMMITTER_NAME="${AUTOSYNC_GIT_NAME:-wwds-dev}" \
  GIT_COMMITTER_EMAIL="${AUTOSYNC_GIT_EMAIL:-243015673+wwds-dev@users.noreply.github.com}" \
  GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" \
    git commit-tree "$1" ${2:+-p "$2"} -m "$3"
}

# Files that must not leave the Mac even when gitleaks sees nothing in them:
# key and credential files by name, and formats gitleaks does not read
# (databases, documents, archives — it skips them as binary or by its own
# path allowlist). Matched on the lower-cased path.
bk_sensitive(){
  case "/$1" in */.ssh/*|*/.gnupg/*|*/.aws/*|*/.kube/*) return 0 ;; esac
  case "${1##*/}" in
    .env|.env.*|*.env|.envrc|.netrc|.npmrc|.pypirc|.pgpass|.git-credentials|credentials|credentials.*) return 0 ;;
    *.pem|*.key|*.p12|*.pfx|*.der|*.crt|*.cer|*.jks|*.keystore|*.kdbx|*.keychain|*.keychain-db) return 0 ;;
    *.mobileprovision|*.ovpn|*.ppk|*.tfstate|*.tfstate.*|id_rsa*|id_ed25519*|id_ecdsa*|id_dsa*) return 0 ;;
  esac
  return 1
}
bk_unscannable(){
  case "${1##*/}" in
    *.sqlite|*.sqlite3|*.sqlite-*|*.db|*.db-*|*.pdf|*.doc|*.docx|*.xls|*.xlsx|*.ppt|*.pptx) return 0 ;;
    *.odt|*.ods|*.zip|*.gz|*.tgz|*.bz2|*.xz|*.7z|*.rar|*.tar|*.dmg|*.pkg|*.jar|*.pkl|*.pickle) return 0 ;;
  esac
  return 1
}
# Binary files nobody stores secrets in. Any other new binary is left out:
# gitleaks cannot read it, so the gate could not have cleared it.
bk_plain_media(){
  case "${1##*/}" in
    *.png|*.jpg|*.jpeg|*.gif|*.webp|*.ico|*.icns|*.heic|*.tif|*.tiff|*.bmp) return 0 ;;
    *.mp3|*.wav|*.m4a|*.m4b|*.aac|*.flac|*.ogg|*.mp4|*.mov|*.m4v|*.webm) return 0 ;;
    *.woff|*.woff2|*.ttf|*.otf|*.eot) return 0 ;;
  esac
  return 1
}

# bk_filter <dir> <idx> <base> — take out of a temporary index every new or
# changed file (vs <base>) that must not be backed up, and record why in
# $BK_TMP/left_out. A changed tracked file goes back to its <base> version;
# a new one is dropped. Logs to stderr: callers run inside $(...).
bk_filter(){
  local dir="$1" idx="$2" base="$3" st path lower why sha size
  while IFS= read -r -d '' st && IFS= read -r -d '' path; do
    case "$st" in A|M|T) ;; *) continue ;; esac
    lower="$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')"
    why=""
    if bk_sensitive "$lower"; then
      why="credential file"
    elif bk_unscannable "$lower"; then
      why="format the scanner cannot read"
    else
      sha="$(GIT_INDEX_FILE="$idx" git -C "$dir" rev-parse -q --verify ":$path" 2>/dev/null)" || continue
      size="$(git -C "$dir" cat-file -s "$sha" 2>/dev/null || echo 0)"
      if [ "$size" -gt "$BACKUP_MAX_BYTES" ]; then
        why="over the size limit"
      elif [ "$st" = "A" ] && git -C "$dir" check-ignore -q --no-index -- "$path" 2>/dev/null; then
        why="ignored"   # a `stash -a` entry carries ignored files
      elif [ "$size" -gt 0 ] && ! bk_plain_media "$lower" \
           && [ "$(GIT_INDEX_FILE="$idx" git -C "$dir" diff --cached --numstat "$base" -- "$path" | cut -f1)" = "-" ]; then
        why="binary the scanner cannot read"
      fi
    fi
    [ -n "$why" ] || continue
    if [ "$st" = "A" ]; then
      GIT_INDEX_FILE="$idx" git -C "$dir" rm -q --cached -- "$path" >/dev/null 2>&1
    else
      GIT_INDEX_FILE="$idx" git -C "$dir" reset -q "$base" -- "$path" >/dev/null 2>&1
    fi
    log "  backup: left out $path ($why)" >&2
    printf '%s\n' "$why" >> "$BK_TMP/left_out"
  done < <(GIT_INDEX_FILE="$idx" git -C "$dir" diff --cached --name-status -z --no-renames "$base" 2>/dev/null)
}

# snapshot_worktree <path> <idx> — print the commit this worktree's backup
# ref should point at: its HEAD when clean, else a commit on HEAD holding its
# tracked edits and untracked (not ignored) files. `git stash create` is not
# used: it has no -u, and untracked files are most of what goes missing (15
# of foundry's 17 files). The real index is copied, not used: its stat cache
# keeps this fast, and the copy is the only index written.
snapshot_worktree(){
  local wt="$1" idx="$2" head base real tree empty
  empty="$(git -C "$wt" hash-object -t tree /dev/null)"
  head="$(git -C "$wt" rev-parse -q --verify HEAD 2>/dev/null)"   # empty on an unborn branch
  base="${head:-$empty}"
  real="$(git -C "$wt" rev-parse --path-format=absolute --git-path index 2>/dev/null)"
  rm -f "$idx"
  { [ -f "$real" ] && cp "$real" "$idx" 2>/dev/null; } \
    || GIT_INDEX_FILE="$idx" git -C "$wt" read-tree "$base" || return 1
  GIT_INDEX_FILE="$idx" git -C "$wt" add -A >/dev/null 2>&1 \
    || { rm -f "$idx"; GIT_INDEX_FILE="$idx" git -C "$wt" read-tree "$base" \
         && GIT_INDEX_FILE="$idx" git -C "$wt" add -A >/dev/null 2>&1; } || return 1
  bk_filter "$wt" "$idx" "$base"
  tree="$(GIT_INDEX_FILE="$idx" git -C "$wt" write-tree)" || return 1
  if [ "$tree" = "$(git -C "$wt" rev-parse "$base^{tree}")" ]; then
    printf '%s\n' "$head"   # clean (nothing on an unborn, empty branch)
    return 0
  fi
  echo dirty >> "$BK_TMP/dirty"
  bk_commit "$tree" "$head" "autosync backup: uncommitted work"   # worktrees share one object store
}

# A stash entry is a merge of HEAD, the index and (with -u/-a) an untracked
# commit; a merge shows the scanner no diff. Flatten it into one ordinary
# commit on its base, filtered like a worktree, so gitleaks sees every file
# that is pushed and ignored files stay behind.
flatten_stash(){   # flatten_stash <stash sha> <idx>
  local w="$1" idx="$2" tree
  rm -f "$idx"
  GIT_INDEX_FILE="$idx" git read-tree "$w^{tree}" || return 1
  if git rev-parse -q --verify "$w^3" >/dev/null 2>&1; then
    git ls-tree -r --full-tree "$w^3" | GIT_INDEX_FILE="$idx" git update-index --index-info || return 1
  fi
  bk_filter "$PWD" "$idx" "$w^1"
  tree="$(GIT_INDEX_FILE="$idx" git write-tree)" || return 1
  bk_commit "$tree" "$(git rev-parse "$w^1")" "autosync backup: stash $(git log -1 --format=%s "$w")"
}

# backup_plan <label>
# Builds, without pushing, the "<sha> <ref>" lines this repo should have in
# the backup repo ($BK_TMP/desired). Runs in the repo's main worktree.
# A worktree or stash that cannot be read is recorded and skipped, so one bad
# file does not stop the rest of the repo from being backed up.
BK_NB=0; BK_NW=0; BK_NS=0
backup_plan(){
  local label="$1" prefix line name snap cur="" gd prunable=0 bare=0 first=1
  BK_NB=0; BK_NW=0; BK_NS=0
  prefix="refs/backup/$(ref_key "$label")"
  : > "$BK_TMP/desired"; : > "$BK_TMP/left_out"; : > "$BK_TMP/failed"; : > "$BK_TMP/dirty"

  while IFS= read -r line; do
    git check-ref-format "$prefix/heads/${line#refs/heads/}" || continue
    echo "$(git rev-parse "$line") $prefix/heads/${line#refs/heads/}" >> "$BK_TMP/desired"
    BK_NB=$((BK_NB+1))
  done < <(git for-each-ref --format='%(refname)' refs/heads)

  # Every worktree, the main one first, keyed by git's own worktree id (the
  # folder name alone is not unique). A prunable one (folder deleted) has
  # nothing left to save.
  emit_wt(){
    [ -n "$cur" ] || return 0
    if [ "$first" -eq 1 ]; then
      first=0; name="main"
    else
      gd="$(git -C "$cur" rev-parse --absolute-git-dir 2>/dev/null)"
      name="wt/$(ref_key "$(basename "${gd:-$cur}")")"
    fi
    { [ "$prunable" -eq 1 ] || [ "$bare" -eq 1 ]; } && return 0
    snap="$(snapshot_worktree "$cur" "$BK_TMP/index")" \
      || { log "  backup: could not snapshot $cur"; echo "$cur" >> "$BK_TMP/failed"; return 0; }
    [ -n "$snap" ] || return 0
    echo "$snap $prefix/wip/$name" >> "$BK_TMP/desired"
    BK_NW=$((BK_NW+1))
  }
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) emit_wt; cur="${line#worktree }"; prunable=0; bare=0 ;;
      prunable*) prunable=1 ;;
      bare) bare=1 ;;
    esac
  done < <(git worktree list --porcelain; echo)
  emit_wt

  while IFS= read -r line; do
    [ -n "$line" ] || continue
    snap="$(flatten_stash "$line" "$BK_TMP/index")" \
      || { log "  backup: could not read stash $line"; echo "stash $line" >> "$BK_TMP/failed"; continue; }
    echo "$snap $prefix/stash/$line" >> "$BK_TMP/desired"
    BK_NS=$((BK_NS+1))
  done < <(git stash list --format=%H 2>/dev/null)
  LC_ALL=C sort -o "$BK_TMP/desired" "$BK_TMP/desired"
}

# Scan exactly what the backup will push and the history scan did not see:
# every planned SHA (branch tips, snapshots, stashes) minus what is reachable
# from a ref now. Snapshots are reachable from no ref; a branch tip recorded
# by the plan and moved since (amend, reset, rebase mid-run) is no longer
# reachable either, and would otherwise be pushed unscanned (review P1-1).
scan_unreached(){
  local dir="$1" shas rc
  # Single spaces only: gitleaks splits --log-opts on every space, and a
  # doubled one becomes an empty argument git rejects.
  shas="$(awk '{print $1}' "$BK_TMP/desired" | LC_ALL=C sort -u | paste -sd ' ' -)"
  [ -n "$shas" ] || return 0
  # shellcheck disable=SC2086
  if ! git rev-list --quiet $shas --not --all >/dev/null 2>&1; then
    log "  git cannot read the commits planned for the backup"
    return 2
  fi
  gl_config "$dir"
  gl_run git --no-banner --redact --verbose --config "$GL_CFG" \
    --log-opts="$shas --not --all $GL_LOG_OPTS" "$dir"; rc=$?
  return $rc
}

# The backup repo must be private: hidden refs on a public repo are public,
# and they carry public repos' unfinished work. Checked once per run, with
# gh, before anything is pushed. A local path is only accepted for tests.
BK_PRE=""
bk_preflight(){
  local slug vis
  [ -n "$BK_PRE" ] && return 0
  case "$BACKUP_REMOTE" in
    https://github.com/*)
      slug="${BACKUP_REMOTE#https://github.com/}"; slug="${slug%.git}"
      case "$slug" in
        */*/*|*[!A-Za-z0-9._/-]*|/*|*/) BK_PRE="the backup URL is not a GitHub repo URL"; return 0 ;;
      esac
      vis="$("$GH" repo view "$slug" --json visibility -q .visibility 2>/dev/null)"
      if [ "$vis" = "PRIVATE" ]; then BK_PRE="ok"
      else BK_PRE="cannot confirm the backup repo is private (gh: ${vis:-no answer})"; fi ;;
    /*)
      if [ "${AUTOSYNC_BACKUP_ALLOW_LOCAL:-0}" = "1" ] && [ -d "$BACKUP_REMOTE" ]; then BK_PRE="ok"
      else BK_PRE="the backup remote must be a private GitHub repo"; fi ;;
    *) BK_PRE="the backup remote must be https://github.com/<owner>/<repo>.git" ;;
  esac
}

# Reasons this repo's own git config would send the push somewhere else.
bk_redirected(){
  local v
  [ "$(git ls-remote --get-url "$BACKUP_REMOTE" 2>/dev/null)" = "$BACKUP_REMOTE" ] || return 0
  while IFS= read -r v; do
    v="${v#* }"
    case "$BACKUP_REMOTE" in "$v"*) return 0 ;; esac
  done < <(git config --get-regexp '^url\..*\.pushinsteadof$' 2>/dev/null)
  return 1
}

# Commits about to be pushed whose author or committer is not allowed.
bk_foreign_identities(){
  local shas
  shas="$(awk '{print $1}' "$BK_TMP/push" | paste -sd ' ' -)"
  [ -n "$shas" ] || return 0
  # shellcheck disable=SC2086
  git log --format='%H %ae %ce' $shas --not --remotes 2>/dev/null \
    | awk -v ok=" $BACKUP_EMAILS " 'index(ok, " " $2 " ") == 0 || index(ok, " " $3 " ") == 0 { print $1 }'
}

# backup_push <label> — compare the plan with the backup repo and push what
# differs. Reports one "BACKUP <label> (ok|partial|missing|failed: ...)"
# summary line. "partial" means pushed, but something was left out or could
# not be read, so the row must not say "Backed up". Stale refs (a branch
# deleted here) are kept, by the owner's decision, and only counted.
BK_STATE=""
bk_report(){   # bk_report <state> <detail>
  BK_STATE="$1"
  SUMMARY+=("BACKUP  $BK_LABEL  ($1: $2)")
  [ "$1" = "failed" ] && N_ERR=$((N_ERR+1))
  return 0
}
backup_push(){
  local prefix out rc n_push n_stale n_left n_failed n_dirty what state bad sha ref
  local -a specs
  BK_LABEL="$1"
  prefix="refs/backup/$(ref_key "$BK_LABEL")/"
  n_dirty="$(wc -l < "$BK_TMP/dirty" | tr -d ' ')"
  what="$BK_NB branch(es), $BK_NW worktree(s) ($n_dirty with uncommitted work), $BK_NS stash(es)"
  n_left="$(wc -l < "$BK_TMP/left_out" | tr -d ' ')"
  n_failed="$(wc -l < "$BK_TMP/failed" | tr -d ' ')"
  state="ok"
  [ "$n_left" -gt 0 ] && { what="$what; $n_left file(s) left out - see log"; state="partial"; }
  [ "$n_failed" -gt 0 ] && { what="$what; $n_failed worktree/stash(es) unreadable - see log"; state="partial"; }

  bk_preflight
  if [ "$BK_PRE" != "ok" ]; then
    log "  backup: FAILED — $BK_PRE"
    bk_report failed "$BK_PRE"; return
  fi
  if bk_redirected; then
    log "  backup: FAILED — this repo's git config rewrites the backup URL (url.*.insteadOf)"
    bk_report failed "git config redirects the backup URL"; return
  fi
  out="$(git ls-remote "$BACKUP_REMOTE" "${prefix}*" 2>&1)"; rc=$?
  if [ $rc -ne 0 ]; then
    printf '%s\n' "$out" >>"$LOG"
    log "  backup: FAILED — cannot reach the backup repo"
    bk_report failed "cannot reach the backup repo - see log"; return
  fi
  printf '%s\n' "$out" | awk -v p="$prefix" 'index($2, p) == 1 { print $1 " " $2 }' \
    | LC_ALL=C sort > "$BK_TMP/remote"
  LC_ALL=C comm -23 "$BK_TMP/desired" "$BK_TMP/remote" > "$BK_TMP/push"
  n_push="$(wc -l < "$BK_TMP/push" | tr -d ' ')"
  n_stale="$(awk '{print $2}' "$BK_TMP/desired" | LC_ALL=C sort \
    | LC_ALL=C comm -13 - <(awk '{print $2}' "$BK_TMP/remote" | LC_ALL=C sort) | wc -l | tr -d ' ')"
  [ "$n_stale" -gt 0 ] && what="$what; $n_stale older backup ref(s) kept"

  if [ "$n_push" -eq 0 ]; then
    log "  backup: up to date ($what)."
    bk_report "$state" "$what"; return
  fi
  bad="$(bk_foreign_identities)"
  if [ -n "$bad" ]; then
    log "  backup: FAILED — commit(s) by an identity not in AUTOSYNC_ALLOWED_EMAILS:"
    printf '%s\n' "$bad" | sed 's/^/      /' | tee -a "$LOG"
    bk_report failed "commits by an identity that may not be pushed - see log"; return
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    log "  DRY-RUN: backup would push $n_push ref(s):"
    sed 's/^[0-9a-f]* /      /' "$BK_TMP/push" | tee -a "$LOG"
    bk_report missing "$n_push ref(s) not backed up yet; $what"; return
  fi
  # An array, not xargs: xargs reads quotes, and ' is valid in a branch name.
  specs=()
  while read -r sha ref; do specs+=("+$sha:$ref"); done < "$BK_TMP/push"
  # --no-verify: a project's pre-push hook is about its own origin, not this.
  out="$(git push -q --atomic --no-verify "$BACKUP_REMOTE" "${specs[@]}" 2>&1)"; rc=$?
  printf '%s\n' "$out" >>"$LOG"
  if [ $rc -eq 0 ]; then
    log "  backup: pushed $n_push ref(s) ($what)."
    bk_report "$state" "$what"
  else
    log "  backup: FAILED — push to the backup repo was rejected (see log)"
    bk_report failed "push to the backup repo failed - see log"
  fi
}

# The branch a sweep may commit to. origin/HEAD when the clone recorded it,
# else main or master, else whatever is checked out (the old behaviour).
default_branch(){
  local d
  d="$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null)"
  if [ -n "$d" ]; then printf '%s' "${d#origin/}"; return; fi
  for d in main master; do
    git show-ref -q --verify "refs/heads/$d" && { printf '%s' "$d"; return; }
  done
  git branch --show-current
}

declare -a SUMMARY
N_SYNCED=0; N_BLOCKED=0; N_SKIP=0; N_NOOP=0; N_ERR=0; N_UNLISTED=0

# Undo this run's own `git add -A`. Only a sweep staged anything: in push-only
# the index is the user's, and resetting it unstaged their work (every
# push-only dry-run did, until 2026-10-20). Reads process_repo's $mode.
unstage(){ [ "$mode" = "sweep" ] && git reset -q 2>/dev/null; return 0; }

# process_repo <config entry> [sweep|push-only]
#
# sweep      — stage the whole working tree, commit it, push. The original
#              behaviour, and the right one for a repo nobody edits by hand
#              between runs.
# push-only  — push commits that already exist and touch nothing else. For any
#              repo a person or an agent works in: a sweep there rolls several
#              unrelated half-finished changes into one commit called
#              "autosync: <timestamp>", which is worse than not backing them up,
#              because it looks like history and is very hard to unpick later.
process_repo(){
  local entry="$1"
  local mode="${2:-sweep}"
  local dir name label branch rc needs_create=0 push_out push_rc reason behind
  dir="$(resolve_repo "$entry")"
  # label identifies the repo everywhere it is reported: it is the config entry
  # verbatim, so the GUI (which keys its rows by config entry) can match. name
  # stays the bare directory name, which is what GitHub gets called.
  label="$entry"
  name="$(basename "$dir")"
  rule
  log "REPO: $label  ($dir)"

  # -e, not -d: a submodule or --separate-git-dir checkout has a .git file.
  if [ ! -e "$dir/.git" ]; then
    log "  SKIP: not a git repository"
    SUMMARY+=("SKIP    $label  (not a git repo)"); N_SKIP=$((N_SKIP+1)); return
  fi
  cd "$dir" || { log "  SKIP: cannot cd"; SUMMARY+=("SKIP    $label  (cd failed)"); N_SKIP=$((N_SKIP+1)); return; }

  # Three cases that used to return here, before the gate, now only stop the
  # origin half: the backup is what protects a repo with no remote or a
  # conflict in progress, so it still runs. None of them may stage anything.
  local no_origin=0 conflict=0 backup_only=0 cur def
  if ! git remote get-url origin >/dev/null 2>&1; then
    if [ -n "$CREATE_REMOTE" ] && { [ "$label" = "$ONLY" ] || [ "$name" = "$ONLY" ]; }; then
      log "  no 'origin' remote yet — will create a $CREATE_REMOTE GitHub repo after the gate clears."
      needs_create=1
    else
      log "  no 'origin' remote (run the GitHub setup script first) - backup only"
      no_origin=1; mode="push-only"
    fi
  fi

  # A conflict left unresolved (by a rebase, merge or stash pop) must never be
  # swept: `git add -A` marks the files resolved, conflict markers and all.
  local gd; gd="$(git rev-parse --git-dir 2>/dev/null)"
  if [ -n "$(git ls-files -u 2>/dev/null)" ] || [ -d "$gd/rebase-merge" ] \
     || [ -d "$gd/rebase-apply" ] || [ -f "$gd/MERGE_HEAD" ]; then
    log "  unresolved conflict or rebase/merge in progress - backup only"
    conflict=1; mode="push-only"
  fi

  # A sweep commits only on the default branch. On a side branch (or a
  # detached HEAD) it would commit there and `push -u` would publish the
  # branch; side branches go to the backup repo, not to GitHub as branches
  # (owner, 2026-10-20). So that run commits nothing and leaves origin alone.
  cur="$(git branch --show-current)"
  if [ "$mode" = "sweep" ]; then
    def="$(default_branch)"
    if [ -z "$cur" ] || [ "$cur" != "$def" ]; then
      log "  sweep: on '${cur:-detached HEAD}', not the default branch '$def' - committing nothing"
      mode="push-only"
      [ -n "$BACKUP_REMOTE" ] && backup_only=1
    fi
  fi

  # Only a sweep writes the real index. Its lock is removed only when it is
  # stale (over 10 minutes): a fresh one belongs to a git command that is
  # running now, and taking the index from under it can lose the index.
  if [ "$mode" = "sweep" ]; then
    local lock; lock="$(git rev-parse --git-path index.lock)"
    if [ -f "$lock" ] && [ -n "$(find "$lock" -mmin +10 2>/dev/null)" ]; then
      rm -f "$lock"; log "  removed a stale index.lock (over 10 minutes old)"
    elif [ -f "$lock" ]; then
      log "  another git command holds the index - committing nothing this run"
      mode="push-only"
    fi
  fi

  if [ "$mode" = "push-only" ]; then
    log "  mode: push-only (working tree left alone)"
  else
    git add -A
  fi

  if [ -n "$BACKUP_REMOTE" ]; then
    backup_plan "$label"
  fi

  # ---- THE GATE ----
  # History is scanned in both modes: it covers everything about to be
  # published. The staged scan is skipped in push-only because nothing was
  # staged — scanning an empty index would pass trivially and read as a
  # clean result that was never actually performed.
  log "  scanning for secrets..."
  if [ "$mode" = "push-only" ]; then
    rc=0
  else
    scan_staged "$dir"; rc=$?
  fi
  if [ $rc -eq 1 ]; then
    log "  BLOCKED: gitleaks found a secret in your changes. Nothing committed or pushed."
    unstage
    SUMMARY+=("BLOCKED $label  (secret in changes - see log)"); N_BLOCKED=$((N_BLOCKED+1)); return
  elif [ $rc -ge 2 ]; then
    log "  ERROR: gitleaks failed to run on staged changes. Skipping for safety."
    unstage
    SUMMARY+=("ERROR   $label  (scanner error)"); N_ERR=$((N_ERR+1)); return
  fi
  scan_history "$dir"; rc=$?
  if [ $rc -eq 1 ]; then
    log "  BLOCKED: a secret exists in this repo's git HISTORY. Refusing to push."
    log "           Clean the history before publishing (see README)."
    unstage
    SUMMARY+=("BLOCKED $label  (secret in history - see log)"); N_BLOCKED=$((N_BLOCKED+1)); return
  elif [ $rc -ge 2 ]; then
    log "  ERROR: gitleaks failed on history. Skipping for safety."
    unstage
    SUMMARY+=("ERROR   $label  (scanner error)"); N_ERR=$((N_ERR+1)); return
  fi
  rc=0
  if [ -n "$BACKUP_REMOTE" ]; then
    scan_unreached "$dir"; rc=$?
  fi
  if [ $rc -eq 1 ]; then
    log "  BLOCKED: a secret is in work the backup would push (uncommitted work, a stash,"
    log "           or a branch tip that moved during this run). Nothing pushed."
    unstage
    SUMMARY+=("BLOCKED $label  (secret in uncommitted work - see log)"); N_BLOCKED=$((N_BLOCKED+1)); return
  elif [ $rc -ge 2 ]; then
    log "  ERROR: gitleaks failed on the work the backup would push. Skipping for safety."
    unstage
    SUMMARY+=("ERROR   $label  (scanner error)"); N_ERR=$((N_ERR+1)); return
  fi
  log "  clean ."

  branch="${cur:-main}"

  # Commits made but not yet pushed. A real run pushes these regardless, but
  # without counting them a dry-run reports "nothing to commit" and the GUI
  # badges the repo Clean while a push is still pending.
  ahead=0; behind=0
  if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    # Fetch first: without it @{u} is whatever was last seen locally, so a
    # remote that moved on looks like "nothing to do" right up until the push
    # is rejected with "fetch first".
    git fetch -q origin 2>/dev/null || true
    ahead="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
    behind="$(git rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"
  fi

  # Backed up before origin is touched, so a rejected or failed push to
  # origin still leaves the branches and worktrees in the backup repo. (A
  # sweep's own commit is made after this, and reaches the backup next run.)
  BK_STATE=""
  if [ -n "$BACKUP_REMOTE" ]; then
    backup_push "$label"
  fi

  if [ "$no_origin" -eq 1 ]; then
    SUMMARY+=("SKIP    $label  (no GitHub remote)"); N_SKIP=$((N_SKIP+1)); return
  fi
  if [ "$conflict" -eq 1 ]; then
    SUMMARY+=("ERROR   $label  (unresolved conflict - resolve it first)"); N_ERR=$((N_ERR+1)); return
  fi
  if [ "$backup_only" -eq 1 ]; then
    SUMMARY+=("OK      $label  (sweep: on side branch '${cur:-detached HEAD}' - nothing committed, backed up only)")
    N_NOOP=$((N_NOOP+1)); return
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    if [ "$needs_create" -eq 1 ]; then
      log "  DRY-RUN: would create a $CREATE_REMOTE GitHub repo '$name' and push."
      SUMMARY+=("OK      $label  (dry-run: would create $CREATE_REMOTE repo + push)")
    elif [ "$mode" = "push-only" ]; then
      if [ "${behind:-0}" -gt 0 ]; then
        # The same blocker the two branches below check for. push-only was the
        # one dry-run path that skipped it, so a repo behind its remote was
        # reported OK and the real run's push was then rejected with "fetch
        # first" — the surprise this whole check exists to stop. push-only is
        # if anything the likelier place to hit it: these are the repos someone
        # is working in, so they are the ones that get pulled on another
        # machine and left behind here.
        log "  DRY-RUN: push-only — behind remote by $behind commit(s) — a push would be REJECTED. Pull first."
        SUMMARY+=("ERROR   $label  (dry-run: behind remote by $behind - pull first)")
        N_ERR=$((N_ERR+1)); unstage; return
      elif [ "$ahead" -gt 0 ]; then
        log "  DRY-RUN: push-only — would push $ahead commit(s):"
        git log --oneline '@{u}..HEAD' | sed 's/^/      /' | tee -a "$LOG"
        SUMMARY+=("OK      $label  (dry-run: would push $ahead commit(s), push-only)")
      else
        log "  DRY-RUN: push-only — nothing committed to push."
        SUMMARY+=("OK      $label  (dry-run: nothing to push)")
      fi
    elif git diff --cached --quiet; then
      if [ "${behind:-0}" -gt 0 ]; then
        # A dry-run that ignores this reports "safe" and the real sync is then
        # rejected with "fetch first" — exactly the surprise it exists to stop.
        log "  DRY-RUN: behind remote by $behind commit(s) — a push would be REJECTED. Pull first."
        SUMMARY+=("ERROR   $label  (dry-run: behind remote by $behind - pull first)")
        N_ERR=$((N_ERR+1)); unstage; return
      elif [ "$ahead" -gt 0 ]; then
        log "  DRY-RUN: no file changes, but $ahead unpushed commit(s) would be pushed:"
        git log --oneline '@{u}..HEAD' | sed 's/^/      /' | tee -a "$LOG"
        SUMMARY+=("OK      $label  (dry-run: would push $ahead unpushed commit(s))")
      else
        log "  DRY-RUN: clean, nothing to commit."
        SUMMARY+=("OK      $label  (dry-run: nothing to commit)")
      fi
    else
      log "  DRY-RUN: would commit & push these staged changes:"
      git diff --cached --name-status | sed 's/^/      /' | tee -a "$LOG"
      if [ "$ahead" -gt 0 ]; then
        log "      (plus $ahead earlier unpushed commit(s))"
      fi
      if [ "${behind:-0}" -gt 0 ]; then
        log "      WARNING: behind remote by $behind commit(s) — the push would be rejected. Pull first."
        SUMMARY+=("ERROR   $label  (dry-run: behind remote by $behind - pull first)")
        N_ERR=$((N_ERR+1)); unstage; return
      fi
      SUMMARY+=("OK      $label  (dry-run: would sync)")
    fi
    unstage
    N_NOOP=$((N_NOOP+1)); return
  fi

  # ---- commit (only if there is something staged) ----
  if [ "$mode" = "push-only" ]; then
    # These two are different situations and were reported as one. `ahead` is
    # only counted when an upstream exists, so a feature branch that has never
    # been published also reads as "0 commits ahead" — which logged "nothing to
    # push" while sitting on commits, and made the refusal to publish it look
    # like there had been nothing to publish.
    if ! git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      log "  push-only: '${cur:-detached HEAD}' has no upstream — not publishing a new branch."
      if [ "$BK_STATE" = "ok" ] || [ "$BK_STATE" = "partial" ]; then
        # Not a problem any more: the branch is in the backup repo, which is
        # where a local-only branch belongs. A partial backup is reported on
        # its own BACKUP line.
        SUMMARY+=("OK      $label  (push-only: '${cur:-detached HEAD}' is local-only, backed up)")
        N_NOOP=$((N_NOOP+1)); return
      fi
      SUMMARY+=("SKIP    $label  (push-only: '${cur:-detached HEAD}' is unpublished)")
      N_SKIP=$((N_SKIP+1)); return
    fi
    if [ "$ahead" -eq 0 ]; then
      log "  push-only: nothing committed to push; working tree untouched."
      SUMMARY+=("OK      $label  (nothing to push)"); N_NOOP=$((N_NOOP+1)); return
    fi
    log "  push-only: $ahead commit(s) to push; working tree untouched."
  elif git diff --cached --quiet; then
    if [ "$ahead" -gt 0 ]; then
      log "  no file changes to commit; pushing $ahead earlier commit(s)."
    else
      log "  no file changes to commit."
    fi
  else
    # Pin the identity rather than inheriting git config: a machine-wide
    # user.email put the real address into 78 commits across 7 public repos.
    git -c user.name="${AUTOSYNC_GIT_NAME:-wwds-dev}" \
        -c user.email="${AUTOSYNC_GIT_EMAIL:-243015673+wwds-dev@users.noreply.github.com}" \
        commit -q -m "${AUTOSYNC_COMMIT_MSG:-autosync: $(ts)}" && log "  committed changes."
  fi

  # ---- create the GitHub repo (first push) or push as usual ----
  if [ "$needs_create" -eq 1 ]; then
    if "$GH" repo create "$name" "--$CREATE_REMOTE" --source=. --remote=origin --push >>"$LOG" 2>&1; then
      log "  created $CREATE_REMOTE GitHub repo and pushed origin/$branch."
      SUMMARY+=("SYNCED  $label  (new $CREATE_REMOTE repo)"); N_SYNCED=$((N_SYNCED+1))
    else
      log "  ERROR: gh repo create failed (see log)."
      SUMMARY+=("ERROR   $label  (repo creation failed)"); N_ERR=$((N_ERR+1))
    fi
    return
  fi

  # ---- push (covers new commit AND any earlier unpushed commits) ----
  if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    push_out="$(git push 2>&1)"; push_rc=$?
    printf '%s\n' "$push_out" >>"$LOG"
    if [ $push_rc -eq 0 ]; then
      if [ "${behind:-0}" -gt 0 ]; then
        # Nothing was wrong locally, but the remote is ahead and this tool
        # never pulls — say so rather than reporting a clean sync.
        log "  pushed to origin/$branch; remote is $behind commit(s) ahead of you (run git pull)."
        SUMMARY+=("SYNCED  $label  (behind remote by $behind - run git pull)")
      else
        log "  pushed to origin/$branch."; SUMMARY+=("SYNCED  $label")
      fi
      N_SYNCED=$((N_SYNCED+1))
    else
      # Name the cause. "push failed" sent the user to a log they had to read
      # themselves; these are the cases that actually happen.
      case "$push_out" in
        *"fetch first"*|*"non-fast-forward"*|*"behind its remote"*)
          reason="remote has ${behind:-newer} commit(s) you do not have — pull first" ;;
        *"Authentication failed"*|*"could not read Username"*|*"Permission denied"*|*"403"*)
          reason="not authorised to push — check gh auth / credentials" ;;
        *"Could not resolve host"*|*"Operation timed out"*|*"Connection reset"*|*"RPC failed"*)
          reason="network problem reaching GitHub — transient, try again" ;;
        *"Repository not found"*|*"does not appear to be a git repository"*)
          reason="remote repo not found — renamed, deleted, or wrong URL" ;;
        *"protected branch"*|*"pre-receive hook declined"*)
          reason="rejected by a branch protection rule on GitHub" ;;
        *) reason="push failed — see log" ;;
      esac
      log "  ERROR: $reason"
      SUMMARY+=("ERROR   $label  ($reason)"); N_ERR=$((N_ERR+1))
    fi
  else
    if git push -u origin "$branch" >>"$LOG" 2>&1; then log "  pushed & set upstream origin/$branch."; SUMMARY+=("SYNCED  $label"); N_SYNCED=$((N_SYNCED+1))
    else log "  ERROR: push failed (see log)."; SUMMARY+=("ERROR   $label  (push failed)"); N_ERR=$((N_ERR+1)); fi
  fi
}

# --- iterate config --------------------------------------------------------
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"; line="$(echo "$line" | xargs)"   # strip comments + trim
  [ -z "$line" ] && continue
  # A line is "<entry> [flags...]". Everything after the first field is a mode
  # flag, so the entry itself stays exactly what it always was — the GUI keys
  # its rows on that string and --repo matches against it.
  entry="${line%% *}"
  flags=" ${line#"$entry"} "
  mode="sweep"
  case "$flags" in *" push-only "*) mode="push-only" ;; esac
  # --repo accepts the config entry ("sentinel/vpn_agent") or its bare
  # name ("vpn_agent"); the GUI sends the entry, a human usually sends the name.
  if [ -n "$ONLY" ] && [ "$entry" != "$ONLY" ] \
     && [ "$(basename "$(resolve_repo "$entry")")" != "$ONLY" ]; then continue; fi
  process_repo "$entry" "$mode"
done < "$CONFIG"

# --- repos on disk that nothing backs up -----------------------------------
# A repo missing from the list is not backed up at all, and used to be
# invisible. Scan roots are "<dir>=<depth>" separated by ':'; depth 0 checks
# the directory itself only (the lab root, whose children are other repos).
# Linked worktrees are skipped: they belong to a listed repo and are
# snapshotted with it. Symlinks are not followed.
SCAN_ROOTS="${AUTOSYNC_SCAN_ROOTS-$LAB_ACTIVE=4:$HOME/Documents/lab=0:$HOME/Documents/Websites=2}"
find_unlisted(){
  local listed root depth gitp top real line entry rest
  listed="$BK_TMP/listed"; : > "$listed"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"; line="$(echo "$line" | xargs)"; [ -z "$line" ] && continue
    entry="${line%% *}"
    ( cd "$(resolve_repo "$entry")" 2>/dev/null && pwd -P ) >> "$listed"
  done < "$CONFIG"
  rest="$SCAN_ROOTS"
  while [ -n "$rest" ]; do
    root="${rest%%:*}"; [ "$root" = "$rest" ] && rest="" || rest="${rest#*:}"
    depth="${root##*=}"; root="${root%=*}"
    # Digits only: bash arithmetic would evaluate anything else as code.
    case "$depth" in ''|*[!0-9]*) log "  scan root ignored, depth is not a number: $root=$depth"; continue ;; esac
    [ -d "$root" ] || continue
    while IFS= read -r gitp; do
      if [ -f "$gitp" ] && grep -q '/worktrees/' "$gitp" 2>/dev/null; then continue; fi
      top="$(dirname "$gitp")"
      real="$(cd "$top" && pwd -P)" || continue
      grep -qxF "$real" "$listed" && continue
      echo "$real" >> "$listed"   # once, even when two roots overlap
      local why="not in the repo list"
      git -C "$top" remote get-url origin >/dev/null 2>&1 || why="$why; no GitHub remote"
      log "UNLISTED: ${top/#$HOME/~}  ($why)"
      SUMMARY+=("UNLISTED ${top/#$HOME/~}  ($why)"); N_UNLISTED=$((N_UNLISTED+1))
    done < <(find "$root" -maxdepth $((depth+1)) -name .git \( -type d -o -type f \) \
               -not -path '*/.claude/*' -not -path '*/node_modules/*' -not -path '*/.venv/*' \
               2>/dev/null | LC_ALL=C sort)
  done
}
if [ -z "$ONLY" ] && [ -n "$SCAN_ROOTS" ]; then
  rule
  find_unlisted
fi

# --- summary ---------------------------------------------------------------
rule
log "SUMMARY:"
for s in "${SUMMARY[@]:-}"; do [ -n "$s" ] && log "   $s"; done
log "synced=$N_SYNCED blocked=$N_BLOCKED skipped=$N_SKIP errors=$N_ERR noop=$N_NOOP unlisted=$N_UNLISTED"
log "=== git_autosync end ==="

# Record when a real (non-dry-run) run last completed, regardless of outcome,
# plus a one-word status ("ok" / "attention") — the GUI's tray icon and the
# scheduled background job both read these two files.
if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$STATE_DIR"
  ts > "$STATE_DIR/last_sync.txt"
  # An unlisted repo is not an error in this run, but it is something on this
  # Mac that nothing backs up, so the tray asks for attention.
  if [ $((N_BLOCKED + N_ERR + N_UNLISTED)) -eq 0 ]; then
    echo "ok" > "$STATE_DIR/last_status.txt"
    # Only a clean run counts as a success. last_sync.txt records that a run
    # happened at all, which says nothing about whether the work got out.
    ts > "$STATE_DIR/last_success.txt"
  else
    echo "attention" > "$STATE_DIR/last_status.txt"
  fi
fi

# non-zero exit if anything was blocked or errored (so failures are visible)
[ $((N_BLOCKED + N_ERR)) -eq 0 ]
