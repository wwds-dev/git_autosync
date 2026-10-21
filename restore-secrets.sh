#!/bin/bash
# =============================================================================
#  restore-secrets.sh  —  put .env files and app data back from the vault
# -----------------------------------------------------------------------------
#  The daily backup writes a secrets bundle to the Proton Drive vault
#  (Vault/Credentials-Recovery/Mac-secrets/latest.tar.gz, by
#  _Admin/backup/backup_secrets.sh). This puts each file in it back at its
#  recorded path under ~, readable only by you (mode 600), after checking it
#  against the bundle's MANIFEST.tsv. A file already there that is newer than
#  the one in the bundle is left alone.
#
#  On a new Mac: run restore.sh first (the repos), then this.
#  Usage:
#     ./restore-secrets.sh <bundle.tar.gz>            # restore
#     ./restore-secrets.sh <bundle.tar.gz> --dry-run  # say what it would do
#     ./restore-secrets.sh <bundle.tar.gz> --into DIR # a drill: under DIR, not ~
#
#  backup_manager/docs/specs/secrets-vault.md
# =============================================================================
set -uo pipefail

BUNDLE=""; INTO="$HOME"; DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --into) INTO="${2:-}"; shift; [ -n "$INTO" ] || { echo "--into needs a folder"; exit 2; } ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1"; exit 2 ;;
    *) BUNDLE="$1" ;;
  esac
  shift
done
[ -f "$BUNDLE" ] || { echo "usage: restore-secrets.sh <bundle.tar.gz> [--into DIR] [--dry-run]"; exit 2; }

umask 077
TMP="$(mktemp -d "${TMPDIR:-/tmp}/secrets_restore.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# Only MANIFEST.tsv and files under home/, no absolute paths, no `..`:
# a crafted archive must not write anywhere else.
bad="$(tar -tzf "$BUNDLE" 2>/dev/null | grep -vE '^(MANIFEST\.tsv|home/?|home/.+)$' ; \
       tar -tzf "$BUNDLE" 2>/dev/null | grep -E '(^|/)\.\.(/|$)|^/')"
if [ -n "$bad" ]; then
  echo "ERROR: the bundle holds paths outside its home/ folder - refusing it:"; echo "$bad" | head -5 | sed 's/^/  /'
  exit 1
fi
tar -xzf "$BUNDLE" -C "$TMP" || { echo "ERROR: cannot read the bundle"; exit 1; }
[ -f "$TMP/MANIFEST.tsv" ] || { echo "ERROR: the bundle has no MANIFEST.tsv"; exit 1; }

n_ok=0; n_skip=0; n_bad=0
while IFS=$'\t' read -r path size sha; do
  rel="${path#\~/}"
  case "/$rel/" in */../*|*/./*) echo "  REFUSED  $path"; n_bad=$((n_bad+1)); continue ;; esac
  src="$TMP/home/$rel"; dst="$INTO/$rel"
  if [ ! -f "$src" ] || [ "$(shasum -a 256 "$src" | cut -d' ' -f1)" != "$sha" ]; then
    echo "  CORRUPT  $path (missing or checksum mismatch) - not restored"; n_bad=$((n_bad+1)); continue
  fi
  if [ -e "$dst" ] && [ "$dst" -nt "$src" ]; then
    echo "  KEPT     $path (the file here is newer)"; n_skip=$((n_skip+1)); continue
  fi
  if [ "$DRY_RUN" -eq 1 ]; then echo "  WOULD    $path"; n_ok=$((n_ok+1)); continue; fi
  mkdir -p "$(dirname "$dst")" && cp -p "$src" "$dst" && chmod 600 "$dst" \
    && { echo "  OK       $path"; n_ok=$((n_ok+1)); } \
    || { echo "  FAILED   $path"; n_bad=$((n_bad+1)); }
done < "$TMP/MANIFEST.tsv"

echo ""
echo "restored=$n_ok kept=$n_skip failed=$n_bad"
[ "$n_bad" -eq 0 ]
