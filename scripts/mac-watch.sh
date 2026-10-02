#!/bin/bash
# Mac primary runner for intern-radar (launchd: com.kernsharma.radar-watch).
# Runs the watcher in the dedicated clone; the program itself fetches,
# commits, pushes and replays (RADAR_GIT=1). Secrets come from the Keychain.
set -u
# Body in main(): each run resets the clone, which can rewrite this file while
# bash is still reading it; a function is parsed whole before it runs.
main() {
REPO="$HOME/.local/share/intern-radar/repo"
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
STATE="$HOME/.local/state/intern-radar"
mkdir -p "$STATE"
cd "$REPO" || return 1

LOCK="$STATE/lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && [ "$age" -lt 10800 ]; then
    echo "$(date -u +%FT%TZ) already running (pid $pid)"; return 0
  fi
  rm -rf "$LOCK" && mkdir "$LOCK" || return 1
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
# Bypass osxkeychain: otherwise git uses the owner's own GitHub login (broad
# scopes) instead of the repo-only PAT, and could store the PAT over it.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=
if [ -z "$GITHUB_TOKEN" ]; then
  echo "$(date -u +%FT%TZ) missing Keychain item radar-mac-pat" >&2; return 1
fi

echo "$(date -u +%FT%TZ) start"
"$PY" -m intern_radar
rc=$?
echo "$(date -u +%FT%TZ) exit $rc"
return $rc
}
main
exit $?
