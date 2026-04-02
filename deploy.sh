#!/bin/bash
set -e

APP_DIR="/root/lead-audit"
REPO="https://github.com/marinweb74-crypto/lead-audit.git"
SERVICE="leadaudit"

echo "=== LeadAudit Deploy ==="

# Install system deps
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git > /dev/null

# Clone or pull
if [ -d "$APP_DIR" ]; then
    echo "Pulling latest..."
    cd "$APP_DIR"
    git pull
else
    echo "Cloning repo..."
    git clone "$REPO" "$APP_DIR"
    cd "$APP_DIR"
fi

# Virtual environment
if [ ! -d "venv" ]; then
    echo "Creating venv..."
    python3 -m venv venv
fi

echo "Installing dependencies..."
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

# Config check
if [ ! -f "config.json" ]; then
    cp config.example.json config.json
    echo ""
    echo "!!! IMPORTANT: Edit config.json with your real credentials !!!"
    echo "    nano $APP_DIR/config.json"
    echo ""
fi

# Init DB
venv/bin/python -c "import sys; sys.path.insert(0,'src'); from db import init_db; init_db(); print('DB initialized')"

# Systemd service
cat > /etc/systemd/system/${SERVICE}.service << EOF
[Unit]
Description=LeadAudit Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE}
systemctl restart ${SERVICE}

echo ""
echo "=== Deploy complete ==="
echo "Bot status: $(systemctl is-active ${SERVICE})"
echo ""
echo "Commands:"
echo "  systemctl status ${SERVICE}   - check status"
echo "  journalctl -u ${SERVICE} -f   - view logs"
echo "  systemctl restart ${SERVICE}  - restart bot"
echo "  nano ${APP_DIR}/config.json   - edit config"
