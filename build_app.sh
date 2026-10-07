#!/bin/bash
# Builds the standalone "git_autosync.app" with PyInstaller, ad-hoc signs it,
# and installs it into /Applications.
set -euo pipefail
cd "$(dirname "$0")"

source .venv/bin/activate
uv pip install -q -r requirements.txt pyinstaller

rm -rf build dist
# A frozen bundle has no .git, so record the build it was made from.
python scripts/stamp_version.py

pyinstaller --noconfirm packaging/git_autosync.spec

codesign --force --deep -s - dist/git_autosync.app

rm -rf "/Applications/git_autosync.app"
cp -R dist/git_autosync.app /Applications/

# Remove build artefacts so Spotlight doesn't index a second copy from dist/.
# Tolerate failure: something (Spotlight, a Finder window, an antivirus) can be
# holding a file in dist/ for a moment, and `rm` then prints "Directory not
# empty" — which looked like the build had failed even though the app was
# already copied into /Applications on the line above.
rm -rf build dist 2>/dev/null || {
  sleep 1
  rm -rf build dist 2>/dev/null || echo "Note: could not clear build/ and dist/ — harmless, the app is installed."
}

echo "Installed: /Applications/git_autosync.app"
echo "First launch from Finder: right-click -> Open (unsigned app, ad-hoc signature only)."
