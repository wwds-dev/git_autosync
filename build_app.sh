#!/bin/bash
# Builds the standalone "git_autosync.app" with PyInstaller, ad-hoc signs it,
# and installs it into /Applications.
set -euo pipefail
cd "$(dirname "$0")"

source .venv/bin/activate
uv pip install -q -r requirements.txt pyinstaller

# Build inside a .noindex directory: Spotlight never indexes one, so the
# intermediate bundles PyInstaller writes do not show up as extra
# "git_autosync" apps in Spotlight and Launchpad. Deleting them afterwards was
# not enough — Spotlight indexes them during the build and keeps the stale
# entries after they are gone.
OUT=build.noindex
rm -rf build dist "$OUT"
# A frozen bundle has no .git, so record the build it was made from.
python scripts/stamp_version.py

pyinstaller --noconfirm --distpath "$OUT/dist" --workpath "$OUT/work" packaging/git_autosync.spec

codesign --force --deep -s - "$OUT/dist/git_autosync.app"

rm -rf "/Applications/git_autosync.app"
cp -R "$OUT/dist/git_autosync.app" /Applications/

# Tolerate failure: something (Spotlight, a Finder window, an antivirus) can be
# holding a file in the build output for a moment, and `rm` then prints "Directory not
# empty" — which looked like the build had failed even though the app was
# already copied into /Applications on the line above.
rm -rf "$OUT" 2>/dev/null || {
  sleep 1
  rm -rf "$OUT" 2>/dev/null || echo "Note: could not clear $OUT/ — harmless, the app is installed."
}

echo "Installed: /Applications/git_autosync.app"
echo "First launch from Finder: right-click -> Open (unsigned app, ad-hoc signature only)."
