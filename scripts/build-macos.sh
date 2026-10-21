#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Build this package on macOS.' >&2
  exit 1
fi
# Use the python.org universal2 runtime, not a single-architecture Homebrew Python.
RIVER_BUILD_PYTHON="${RIVER_BUILD_PYTHON:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3}"
if [[ ! -x .venv-build/bin/python ]]; then
  "$RIVER_BUILD_PYTHON" -m venv .venv-build
fi
.venv-build/bin/python -m pip install -r requirements-build.txt
.venv-build/bin/python -m unittest discover -s tests
node --test tests/*.cjs
# Build outside synced folders: Finder/Drive metadata can invalidate app signatures.
RIVER_BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp/}river-macos-build.XXXXXX")"
trap 'rm -rf "$RIVER_BUILD_ROOT"' EXIT
.venv-build/bin/python -m PyInstaller --noconfirm --clean --distpath "$RIVER_BUILD_ROOT/dist" --workpath "$RIVER_BUILD_ROOT/work" River.spec
RIVER_BUILT_APP="$RIVER_BUILD_ROOT/dist/River After School.app"
xattr -cr "$RIVER_BUILT_APP"
"$RIVER_BUILT_APP/Contents/MacOS/River After School" --self-test
codesign --verify --deep --strict "$RIVER_BUILT_APP"
mkdir -p "$RIVER_BUILD_ROOT/dmg" dist
ditto --norsrc --noextattr "$RIVER_BUILT_APP" "$RIVER_BUILD_ROOT/dmg/River After School.app"
cp INSTALL.md "$RIVER_BUILD_ROOT/dmg/Install and transfer instructions.txt"
ln -s /Applications "$RIVER_BUILD_ROOT/dmg/Applications"
codesign --verify --deep --strict "$RIVER_BUILD_ROOT/dmg/River After School.app"
rm -f dist/River-After-School-1.1.0-universal.dmg
hdiutil create -volname 'River After School' -srcfolder "$RIVER_BUILD_ROOT/dmg" -ov -format UDZO dist/River-After-School-1.1.0-universal.dmg
(cd dist && shasum -a 256 River-After-School-1.1.0-universal.dmg > SHA256SUMS.txt)
