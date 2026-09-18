#!/usr/bin/env bash
# PROMETHEUS one-shot deploy for an Oracle Cloud Free Tier (ARM or x86) Ubuntu box.
# Run ON THE SERVER:  bash ops/deploy/oracle-deploy.sh
# Idempotent: safe to re-run. Assumes Ubuntu with sudo available.
set -euo pipefail

echo "◈ PROMETHEUS deploy"
sudo timedatectl set-timezone Asia/Kolkata     # session clock must be IST
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git curl

# ---- 1. clone/pull repo -----------------------------------------------------
REPO_DIR="${REPO_DIR:-$HOME/prometheus}"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone https://github.com/neotennyson/prometheus-fno.git "$REPO_DIR"
fi
cd "$REPO_DIR"
git pull --ff-only origin main || true

# ---- 2. python env ----------------------------------------------------------
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# ---- 3. secrets (.env — you fill it once) ------------------------------------
if [ ! -f .env ]; then cp .env.example .env; fi
if ! grep -q "^TOKEN_API_KEY=..*" .env; then
  echo ""; echo ">>> paste your TOKEN_API_KEY now (input hidden):"
  read -rs K
  sed -i "s|^TOKEN_API_KEY=.*|TOKEN_API_KEY=$K|" .env
fi
if ! grep -q "^PROMETHEUS_DASH_CODE=..*" .env; then
  CODE="PROM-$(openssl rand -hex 4 2>/dev/null || head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  sed -i "s|^PROMETHEUS_DASH_CODE=.*|PROMETHEUS_DASH_CODE=$CODE|" .env
  echo "dashboard access code: $CODE   (write this down)"
fi

# ---- 4. systemd units (argus + dashboard always; session timer weekdays) -----
sudo tee /etc/systemd/system/prometheus-argus.service > /dev/null << EOF
[Unit]
Description=PROMETHEUS ARGUS watchman
After=network-online.target
[Service]
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/python -m src.news_service
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF

mkdir -p ops/deploy
grep -q "^PROMETHEUS_DASH_CODE=" .env && grep "^PROMETHEUS_DASH_CODE=" .env > ops/deploy/dashboard.env
sudo tee /etc/systemd/system/prometheus-dashboard.service > /dev/null << EOF
[Unit]
Description=PROMETHEUS dashboard
After=network-online.target
[Service]
User=$USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$REPO_DIR/ops/deploy/dashboard.env
ExecStart=$REPO_DIR/.venv/bin/streamlit run src/command_center.py --server.port 8501 --server.headless true
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/prometheus-session.service > /dev/null << EOF
[Unit]
Description=EMBER paper session (market hours)
After=network-online.target
[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=/bin/bash $REPO_DIR/scripts/session.sh
TimeoutStartSec=0
EOF

sudo tee /etc/systemd/system/prometheus-session.timer > /dev/null << EOF
[Unit]
Description=Fire EMBER session weekdays 09:05 IST
[Timer]
OnCalendar=Mon..Fri *-*-* 09:05:00
Timezone=Asia/Kolkata
Persistent=true
[Install]
WantedBy=timers.target
EOF

sudo tee /etc/systemd/system/prometheus-data.service > /dev/null << EOF
[Unit]
Description=data refresh after close
[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/python scripts/download_history.py
EOF
sudo tee /etc/systemd/system/prometheus-data.timer > /dev/null << EOF
[Unit]
Description=refresh data 15:40 IST weekdays
[Timer]
OnCalendar=Mon..Fri *-*-* 15:40:00
Timezone=Asia/Kolkata
[Install]
WantedBy=timers.target
EOF

sudo tee /etc/systemd/system/prometheus-evolve.service > /dev/null << EOF
[Unit]
Description=ANVIL evolution generation
[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/python scripts/evolve.py
EOF
sudo tee /etc/systemd/system/prometheus-evolve.timer > /dev/null << EOF
[Unit]
Description=weekly ANVIL generation Sunday 10:00 IST
[Timer]
OnCalendar=Sun *-*-* 10:00:00
Timezone=Asia/Kolkata
[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now prometheus-argus prometheus-dashboard
sudo systemctl enable --now prometheus-session.timer prometheus-data.timer prometheus-evolve.timer

# ---- 5. public tunnel (cloudflared) ------------------------------------------
if [ ! -x "$HOME/bin/cloudflared" ]; then
  ARCH=$(uname -m); [ "$ARCH" = "aarch64" ] && CFARCH=arm64 || CFARCH=amd64
  curl -sL -o /tmp/cf.tgz "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CFARCH}.tgz"
  mkdir -p "$HOME/bin" && tar xzf /tmp/cf.tgz -C "$HOME/bin" && chmod +x "$HOME/bin/cloudflared"
fi
sudo tee /etc/systemd/system/prometheus-tunnel.service > /dev/null << EOF
[Unit]
Description=public tunnel for dashboard
After=network-online.target prometheus-dashboard.service
[Service]
User=$USER
ExecStart=$HOME/bin/cloudflared tunnel --url http://localhost:8501 --no-autoupdate
Restart=always
RestartSec=15
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now prometheus-tunnel
sleep 8
URL=$(sudo journalctl -u prometheus-tunnel --since "1 minute ago" 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
[ -n "$URL" ] && echo "$URL" > "$REPO_DIR/logs/tunnel_url.txt"

echo ""
echo "DONE — PROMETHEUS runs 24/7 on this server:"
systemctl is-active prometheus-argus prometheus-dashboard prometheus-tunnel 2>/dev/null | paste <(echo argus dashboard tunnel) -
systemctl list-timers | grep prometheus || true
echo "public dashboard: $(cat $REPO_DIR/logs/tunnel_url.txt 2>/dev/null || 'check journalctl -u prometheus-tunnel')"
echo "next paper session: next weekday 09:05 IST (systemd timer, holiday-aware)"