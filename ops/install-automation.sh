#!/usr/bin/env bash
# Installs macOS launchd agents so PROMETHEUS runs itself:
#   com.prometheus.argus   — ARGUS watchman, always on, auto-restarts
#   com.prometheus.tunnel  — public dashboard tunnel, always on, auto-restarts
#   com.prometheus.session — EMBER paper session, Mon-Fri 09:05 IST (host clock must be IST)
#   com.prometheus.data    — data refresh Mon-Fri 15:40
#   com.prometheus.evolve  — ANVIL generation, Sundays 10:00
# Run once:  bash ops/install-automation.sh
set -e
cd "$(dirname "$0")/.."
REPO="$(pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$PLIST_DIR" logs

write_plist() {
  local label="$1" program="$2" schedule="$3" keepalive="$4"
  local keep_xml=""
  [ "$keepalive" = "yes" ] && keep_xml="<key>KeepAlive</key><true/>"
  cat > "$PLIST_DIR/$label.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string><string>$program</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  $keep_xml
  <key>StandardOutPath</key><string>$REPO/logs/launchd_$label.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd_$label.log</string>
  $schedule
</dict></plist>
EOF
}

# 1. ARGUS always-on (restarts if it crashes or the Mac reboots)
write_plist "com.prometheus.argus" \
  "cd '$REPO' && bash scripts/argus.sh start; while true; do sleep 3600; done" "" "yes"

# 2. Tunnel always-on (re-establishes the public URL after reboot)
write_plist "com.prometheus.tunnel" \
  "cd '$REPO' && bash scripts/tunnel.sh start; while true; do sleep 3600; done" "" "yes"

# 3. EMBER session: weekdays 09:05 — one launchd plist per weekday
#    (launchd runs in LOCAL time — set the Mac to IST, or on a UTC VPS use hour=3)
for WD in 1 2 3 4 5; do
  cat > "$PLIST_DIR/com.prometheus.session.$WD.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.prometheus.session.$WD</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string><string>cd '$REPO' && bash scripts/session.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>$WD</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>5</integer></dict>
  <key>StandardOutPath</key><string>$REPO/logs/launchd_session.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd_session.log</string>
</dict></plist>
EOF
done
rm -f "$PLIST_DIR/com.prometheus.session.plist"

# 4. data refresh 15:40 weekdays
for WD in 1 2 3 4 5; do
  cat > "$PLIST_DIR/com.prometheus.data.$WD.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.prometheus.data.$WD</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string><string>cd '$REPO' && .venv/bin/python scripts/download_history.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>$WD</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>40</integer></dict>
  <key>StandardOutPath</key><string>$REPO/logs/launchd_data.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd_data.log</string>
</dict></plist>
EOF
done


# 6. token renewal 08:45 Mon-Fri (Groww tokens expire 06:00 daily; session 09:05)
for WD in 1 2 3 4 5; do
  cat > "$PLIST_DIR/com.prometheus.tokens.$WD.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.prometheus.tokens.$WD</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string><string>cd '$REPO' && .venv/bin/python scripts/token_renew.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>$WD</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
  <key>StandardOutPath</key><string>$REPO/logs/launchd_tokens.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd_tokens.log</string>
</dict></plist>
EOF
done

# 5. weekly evolution generation, Sunday 10:00
cat > "$PLIST_DIR/com.prometheus.evolve.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.prometheus.evolve</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string><string>cd '$REPO' && .venv/bin/python scripts/evolve.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$REPO/logs/launchd_evolve.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd_evolve.log</string>
</dict></plist>
EOF

echo "loading agents..."
for f in "$PLIST_DIR"/com.prometheus.*.plist; do
  launchctl unload "$f" 2>/dev/null || true
  launchctl load "$f" && echo "  loaded $(basename $f)"
done

echo ""
echo "DONE. PROMETHEUS now runs itself:"
echo "  - ARGUS + tunnel: always on (auto-restart)"
echo "  - paper session : 09:05 Mon-Fri (holiday-aware)"
echo "  - data refresh  : 15:40 Mon-Fri"
echo "  - evolution     : Sundays 10:00"
echo "Public dashboard URL: bash scripts/tunnel.sh url  (also in logs/tunnel_url.txt)"
echo "Uninstall: launchctl unload ~/Library/LaunchAgents/com.prometheus.*.plist"