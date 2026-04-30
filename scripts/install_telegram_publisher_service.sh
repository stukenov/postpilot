#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="telegram-publisher"
QUEUE_FILE="$PROJECT_DIR/state/telegram-publish-queue.json"

cat >/etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=Telegram Publisher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=threads
Group=threads
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env python3 $PROJECT_DIR/scripts/telegram_publisher.py run --queue-file $QUEUE_FILE
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
