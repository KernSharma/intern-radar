# Mac runner setup (one time)

The Mac is intern-radar's primary runner (every 30 minutes while awake);
GitHub Actions is the backup and skips itself while the Mac is active. Spec:
`docs/specs/2026-09-30-wider-watcher-design.md`, "Runners and the write
protocol".

## 1. Fine-grained token

GitHub → Settings → Developer settings → Fine-grained tokens → Generate:
- Repository access: **only** `intern-radar`
- Permissions: **Contents: Read and write**, **Issues: Read and write**
- Expiry: 1 year (set a reminder)

Store it (you are prompted for the value; it never appears on screen or in
shell history):

    security add-generic-password -s radar-mac-pat -a "$USER" -w

## 2. Dedicated clone

    mkdir -p ~/.local/share/intern-radar ~/.local/state/intern-radar
    git clone https://github.com/KernSharma/intern-radar.git ~/.local/share/intern-radar/repo
    cd ~/.local/share/intern-radar/repo
    git config user.name "intern-radar[mac]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

Never edit files in this clone; each run resets it to `origin/main`.

## 3. Optional notifiers

The ntfy topic is shared with the briefing system (`ntfy-topic` already
exists in the Keychain). For Discord, store the webhook as
`radar-discord-webhook`.

## 4. Test by hand, then schedule

    bash ~/.local/share/intern-radar/repo/scripts/mac-watch.sh
    tail ~/.local/state/intern-radar/launchd.log   # after scheduling

    cp ~/.local/share/intern-radar/repo/launchd/com.kernsharma.radar-watch.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kernsharma.radar-watch.plist

Stop it with `launchctl bootout gui/$(id -u)/com.kernsharma.radar-watch`.

## 5. Actions secret (backup runner health pushes)

Repository → Settings → Secrets → Actions → `NTFY_TOPIC` = the same topic.

## Checking the gates

    cd ~/.local/share/intern-radar/repo
    PYTHONPATH=src python3 scripts/gate_check.py --phase 1 --expect "R3|vanshb03"
    PYTHONPATH=src python3 scripts/regression_replay.py
