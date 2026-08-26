#!/usr/bin/env bash
# ARGUS watchman control: start | stop | status
#   .venv/bin/bash scripts/argus.sh start
set -u
cd "$(dirname "$0")/.."
PIDFILE="logs/argus.pid"
LOG="logs/argus.log"

is_up() { curl -s -m 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:8791/status 2>/dev/null | grep -q 200; }

case "${1:-status}" in
  start)
    if is_up; then echo "ARGUS already running (port 8791)"; exit 0; fi
    echo "starting ARGUS..."
    nohup .venv/bin/python -m src.news_service >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    for i in $(seq 1 20); do
      sleep 0.5
      if is_up; then echo "ARGUS ONLINE (pid $(cat "$PIDFILE")) — journal: $LOG"; exit 0; fi
    done
    echo "ARGUS failed to come up — tail of log:"; tail -5 "$LOG"; exit 1
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE" && echo "ARGUS stopped"
    else
      pkill -f src.news_service 2>/dev/null && echo "ARGUS stopped" || echo "was not running"
    fi
    ;;
  status|*)
    if is_up; then
      curl -s http://127.0.0.1:8791/status | .venv/bin/python -c \
        "import json,sys; d=json.load(sys.stdin); print('ARGUS ONLINE · feeds:', d['feeds_monitored'], '· headlines:', d['headlines_seen'])"
    else
      echo "ARGUS OFFLINE — start with: scripts/argus.sh start"
      exit 1
    fi
    ;;
esac
