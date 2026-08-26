#!/usr/bin/env bash
# One command to bring up the whole organism:
#   .venv/bin/bash scripts/launch.sh
# Starts ARGUS (background, survives terminal close until reboot/stop),
# then runs the PROMETHEUS dashboard in the foreground.
set -e
cd "$(dirname "$0")/.."

echo "◈ PROMETHEUS launcher"
bash scripts/argus.sh start

echo ""
echo "  dashboard → http://localhost:8501"
echo "  argus api → http://127.0.0.1:8791/status  ·  /pulse"
echo "  (Ctrl+C stops the dashboard; ARGUS keeps running —"
echo "   stop it separately with scripts/argus.sh stop)"
echo ""
exec .venv/bin/streamlit run src/command_center.py --server.port 8501
