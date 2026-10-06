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

echo "[session] preflight $TODAY" >> "$LOG"
$PY -c '
from src.config import load_dotenv
load_dotenv()
import os, time
from datetime import datetime, timezone
from src.brokers.tokens import get_token
for i in range(6):
    try:
        get_token("dhan"); print("[preflight] token OK"); raise SystemExit(0)
    except Exception as e:
        tok = os.environ.get("DHAN_ACCESS_TOKEN", "")
        ok = False
        if tok and tok.count(".") == 2:
            try:
                import base64, json as J
                pay = tok.split(".")[1]; pay += "=" * (-len(pay) % 4)
                exp = J.loads(base64.urlsafe_b64decode(pay)).get("exp", 0)
                if exp > datetime.now(timezone.utc).timestamp():
                    ok = True
            except Exception:
                pass
        if ok:
            print("[preflight] API down, manual JWT valid — proceeding"); raise SystemExit(0)
        print(f"[preflight] attempt {i+1} failed: {str(e)[:90]}; retry 90s")
        time.sleep(90)
raise SystemExit(1)
' >> "$LOG" 2>&1 || { echo "[session] ABORT no usable token" >> "$LOG"; exit 1; }

echo "[session] starting EMBER paper loop $TODAY" >> "$LOG"
# run_paper dies before close -> relaunch up to 2x (network hiccups)
for TRY in 1 2 3; do
  $PY scripts/run_paper.py --mode paper >> "$LOG" 2>&1 && break
  echo "[session] run_paper exited (try $TRY) — relaunching in 60s" >> "$LOG"
  sleep 60
done
echo "[session] loop ended $TODAY" >> "$LOG"
