#!/usr/bin/env bash
# Public tunnel for the PROMETHEUS dashboard (Cloudflare quick tunnel, free).
#   bash scripts/tunnel.sh start|stop|status|url
# The public URL changes on restart; it is written to logs/tunnel_url.txt
# and the dashboard itself is protected by PROMETHEUS_DASH_CODE (see .env).
set -u
cd "$(dirname "$0")/.."
CF="${CLOUDFLARED:-$HOME/bin/cloudflared}"
PIDFILE=logs/tunnel.pid
URLFILE=logs/tunnel_url.txt

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "tunnel already running (pid $(cat $PIDFILE))"; bash "$0" url; exit 0
  fi
  [ -x "$CF" ] || { echo "cloudflared not found at $CF"; exit 1; }
  nohup "$CF" tunnel --url http://localhost:8501 --no-autoupdate > logs/tunnel.log 2>&1 &
  echo $! > "$PIDFILE"
  echo "waiting for tunnel URL..."
  for i in $(seq 1 40); do
    sleep 1
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1)
    if [ -n "$URL" ]; then echo "$URL" > "$URLFILE"; echo "PUBLIC URL: $URL"; exit 0; fi
  done
  echo "tunnel failed — see logs/tunnel.log"; exit 1
}
stop() {
  [ -f "$PIDFILE" ] && kill "$(cat $PIDFILE)" 2>/dev/null && rm -f "$PIDFILE"
  pkill -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null
  echo "tunnel stopped"
}
url() {
  [ -f "$URLFILE" ] && cat "$URLFILE" || echo "no url yet"
}
case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  url) url ;;
  *) if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
       echo "tunnel UP · $(cat $URLFILE 2>/dev/null || echo 'url pending')"; else echo "tunnel DOWN"; fi ;;
esac
