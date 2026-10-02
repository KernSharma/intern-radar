#!/bin/bash
# Mac primary runner for intern-radar (launchd: com.kernsharma.radar-watch).
# Runs the watcher in the dedicated clone; the program itself fetches,
# commits, pushes and replays (RADAR_GIT=1). Secrets come from the Keychain.
set -u
REPO="$HOME/.local/share/intern-radar/repo"
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
STATE="$HOME/.local/state/intern-radar"
mkdir -p "$STATE"
cd "$REPO" || exit 1

LOCK="$STATE/lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) already running (pid $pid)"; exit 0
  fi
  rm -rf "$LOCK" && mkdir "$LOCK" || exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

keychain() { security find-generic-password -s "$1" -w 2>/dev/null || true; }

export PYTHONPATH=src RADAR_GIT=1 RADAR_RUNNER=mac RADAR_GITHUB_ISSUES=0
export GITHUB_REPOSITORY="KernSharma/intern-radar"
export GITHUB_TOKEN="$(keychain radar-mac-pat)"
export DISCORD_WEBHOOK_URL="$(keychain radar-discord-webhook)"
export NTFY_TOPIC="$(keychain ntfy-topic)"
export GIT_ASKPASS="$REPO/scripts/git-askpass.sh" GIT_TERMINAL_PROMPT=0
if [ -z "$GITHUB_TOKEN" ]; then
  echo "$(date -u +%FT%TZ) missing Keychain item radar-mac-pat" >&2; exit 1
fi

echo "$(date -u +%FT%TZ) start"
"$PY" -m intern_radar
rc=$?
echo "$(date -u +%FT%TZ) exit $rc"
exit $rc
