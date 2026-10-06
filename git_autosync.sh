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

# Run a gitleaks scan. Returns: 0 = clean, 1 = LEAK found, 2 = tool error.
# Tries modern subcommand first, falls back to legacy for older gitleaks.
# --verbose prints File:/RuleID:/Line: per finding (secret value still
# redacted) so the GUI can show *what* tripped the gate without opening the
# log file. Piped through tee (not >>) so it also reaches stdout — pipefail
# (set above) keeps gitleaks' real exit code, not tee's.
scan_staged(){
  local dir="$1" rc
  "$GITLEAKS" git --staged --no-banner --redact --verbose "$dir" 2>&1 | tee -a "$LOG"; rc=$?
  if [ $rc -ge 2 ]; then
    "$GITLEAKS" protect --staged --no-banner --redact --verbose --source "$dir" 2>&1 | tee -a "$LOG"; rc=$?
  fi
  return $rc
}
scan_history(){
  local dir="$1" rc
  "$GITLEAKS" git --no-banner --redact --verbose "$dir" 2>&1 | tee -a "$LOG"; rc=$?
  if [ $rc -ge 2 ]; then
    "$GITLEAKS" detect --no-banner --redact --verbose --source "$dir" 2>&1 | tee -a "$LOG"; rc=$?
  fi
  return $rc
}

declare -a SUMMARY
N_SYNCED=0; N_BLOCKED=0; N_SKIP=0; N_NOOP=0; N_ERR=0

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

  if [ ! -d "$dir/.git" ]; then
    log "  SKIP: not a git repository"
    SUMMARY+=("SKIP    $label  (not a git repo)"); N_SKIP=$((N_SKIP+1)); return
  fi
  rm -f "$dir/.git/index.lock" 2>/dev/null
  cd "$dir" || { log "  SKIP: cannot cd"; SUMMARY+=("SKIP    $label  (cd failed)"); N_SKIP=$((N_SKIP+1)); return; }

  if ! git remote get-url origin >/dev/null 2>&1; then
    if [ -n "$CREATE_REMOTE" ] && { [ "$label" = "$ONLY" ] || [ "$name" = "$ONLY" ]; }; then
      log "  no 'origin' remote yet — will create a $CREATE_REMOTE GitHub repo after the gate clears."
      needs_create=1
    else
      log "  SKIP: no 'origin' remote (run the GitHub setup script first)"
      SUMMARY+=("SKIP    $label  (no GitHub remote)"); N_SKIP=$((N_SKIP+1)); return
    fi
  fi

  if [ "$mode" = "push-only" ]; then
    log "  mode: push-only (working tree left alone)"
  else
    git add -A
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
    git reset -q 2>/dev/null
    SUMMARY+=("BLOCKED $label  (secret in changes - see log)"); N_BLOCKED=$((N_BLOCKED+1)); return
  elif [ $rc -ge 2 ]; then
    log "  ERROR: gitleaks failed to run on staged changes. Skipping for safety."
    git reset -q 2>/dev/null
    SUMMARY+=("ERROR   $label  (scanner error)"); N_ERR=$((N_ERR+1)); return
  fi
  scan_history "$dir"; rc=$?
  if [ $rc -eq 1 ]; then
    log "  BLOCKED: a secret exists in this repo's git HISTORY. Refusing to push."
    log "           Clean the history before publishing (see README)."
    git reset -q 2>/dev/null
    SUMMARY+=("BLOCKED $label  (secret in history - see log)"); N_BLOCKED=$((N_BLOCKED+1)); return
  elif [ $rc -ge 2 ]; then
    log "  ERROR: gitleaks failed on history. Skipping for safety."
    git reset -q 2>/dev/null
    SUMMARY+=("ERROR   $label  (scanner error)"); N_ERR=$((N_ERR+1)); return
  fi
  log "  clean ."

  branch="$(git branch --show-current)"
  branch="${branch:-main}"

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

  if [ "$DRY_RUN" -eq 1 ]; then
    if [ "$needs_create" -eq 1 ]; then
      log "  DRY-RUN: would create a $CREATE_REMOTE GitHub repo '$name' and push."
      SUMMARY+=("OK      $label  (dry-run: would create $CREATE_REMOTE repo + push)")
    elif [ "$mode" = "push-only" ]; then
      if [ "$ahead" -gt 0 ]; then
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
        N_ERR=$((N_ERR+1)); git reset -q 2>/dev/null; return
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
        N_ERR=$((N_ERR+1)); git reset -q 2>/dev/null; return
      fi
      SUMMARY+=("OK      $label  (dry-run: would sync)")
    fi
    git reset -q 2>/dev/null
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
      log "  push-only: '$branch' has no upstream — not publishing a new branch."
      SUMMARY+=("SKIP    $label  (push-only: '$branch' is unpublished)")
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

# --- summary ---------------------------------------------------------------
rule
log "SUMMARY:"
for s in "${SUMMARY[@]:-}"; do [ -n "$s" ] && log "   $s"; done
log "synced=$N_SYNCED blocked=$N_BLOCKED skipped=$N_SKIP errors=$N_ERR noop=$N_NOOP"
log "=== git_autosync end ==="

# Record when a real (non-dry-run) run last completed, regardless of outcome,
# plus a one-word status ("ok" / "attention") — the GUI's tray icon and the
# scheduled background job both read these two files.
if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$STATE_DIR"
  ts > "$STATE_DIR/last_sync.txt"
  if [ $((N_BLOCKED + N_ERR)) -eq 0 ]; then
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
