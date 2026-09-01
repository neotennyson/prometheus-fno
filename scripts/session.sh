#!/usr/bin/env bash
# EMBER daily paper-session runner — designed for launchd/cron.
# Skips weekends + NSE holidays, waits for open if launched early,
# runs the paper loop, exits after square-off. Logs to logs/sessions/.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
LOGDIR=logs/sessions
mkdir -p "$LOGDIR"
TODAY=$($PY -c "from src.orchestrator import ist_now; print(ist_now().date())")
LOG="$LOGDIR/session_$TODAY.log"

# holiday / weekend gate (calendar module knows NSE holidays)
$PY - << 'EOF' || exit 0
import sys
from src.data.calendar import all_holidays
from src.orchestrator import ist_now
from datetime import date
today = ist_now().date()
if today.weekday() >= 5:
    print(f"[session] {today} weekend — skipping"); sys.exit(1)
if today in set(all_holidays()):
    print(f"[session] {today} NSE holiday — skipping"); sys.exit(1)
print(f"[session] {today} is a trading day — proceeding")
EOF

# wait for market open if we were launched early (max wait ~1h)
while true; do
  M=$($PY -c "from src.orchestrator import ist_now; n=ist_now(); print(n.hour*60+n.minute)")
  if [ "$M" -ge 555 ]; then break; fi
  if [ "$M" -lt 480 ]; then echo "[session] too early, exiting (launched for wrong window?)"; exit 0; fi
  echo "[session] pre-market, waiting..."; sleep 300
done

echo "[session] starting EMBER paper loop $TODAY" >> "$LOG"
# token sanity: if creds missing/invalid the loop still runs but logs skips;
# it also exits cleanly at square-off (15:18) via the orchestrator itself.
$PY scripts/run_paper.py --mode paper >> "$LOG" 2>&1
echo "[session] loop ended $TODAY" >> "$LOG"
