#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="facebook-publisher"
QUEUE_FILE="$PROJECT_DIR/state/facebook-publish-queue.json"

cat >/etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=Facebook Publisher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=threads
Group=threads
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
Environment=PYTHONUNBUFFERED=1
Environment=PLAYWRIGHT_BROWSERS_PATH=$PROJECT_DIR/.playwright-browsers
ExecStart=/usr/bin/env python3 $PROJECT_DIR/scripts/facebook_publisher.py run --queue-file $QUEUE_FILE
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "Service file written to: /etc/systemd/system/$SERVICE_NAME.service"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
echo "Service enabled and started: $SERVICE_NAME"
echo "Check logs with: journalctl -u $SERVICE_NAME -f"
