#!/usr/bin/env bash
# Package the built .app into a distributable disk image.
#
# Tauri's own DMG step drives Finder over AppleScript to lay out the window.
# That needs an interactive session with Automation permission granted, and
# times out otherwise ("AppleEvent timed out, -1712"). hdiutil needs neither,
# so this produces a plain but reliable image that works in CI.
set -euo pipefail
cd "$(dirname "$0")/.."

APP="apps/desktop/src-tauri/target/release/bundle/macos/StudyPilot.app"
VERSION="$(python3 -c 'import json,sys; print(json.load(open("apps/desktop/src-tauri/tauri.conf.json"))["version"])')"
OUT="apps/desktop/src-tauri/target/release/bundle/dmg"
DMG="$OUT/StudyPilot_${VERSION}_aarch64.dmg"

if [[ ! -d "$APP" ]]; then
  echo "No .app found at $APP" >&2
  echo "Build it first:  npm run build:desktop" >&2
  exit 1
fi

echo "==> Staging"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

echo "==> Creating $DMG"
mkdir -p "$OUT"
rm -f "$DMG"
hdiutil create \
  -volname "StudyPilot" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  -fs HFS+ \
  "$DMG" >/dev/null

echo "==> Verifying"
hdiutil verify "$DMG" >/dev/null
ls -lh "$DMG"

cat <<'MSG'

This disk image is unsigned and not notarised. macOS will refuse to open it by
double-click; right-click the app and choose Open, once, to run it.

To sign and notarise, set the usual Tauri environment variables
(APPLE_CERTIFICATE, APPLE_SIGNING_IDENTITY, APPLE_ID, APPLE_PASSWORD,
APPLE_TEAM_ID) before `npm run build:desktop`, then re-run this script.
MSG
