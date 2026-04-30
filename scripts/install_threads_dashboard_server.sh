#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_NAME="${SERVICE_NAME:-threads-dashboard}"
RUN_USER="${RUN_USER:-threads}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8790}"
QUEUE_FILE="${QUEUE_FILE:-$PROJECT_DIR/state/threads-publish-queue.json}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      cat <<EOF
Usage:
  sudo bash scripts/install_threads_dashboard_server.sh [options]

Options:
  --project-dir <path>
  --service-name <name>
  --run-user <user>
  --host <host>
  --port <port>
  --queue-file <path>
EOF
      exit 0
      ;;
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --queue-file)
      QUEUE_FILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=Threads Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env python3 $PROJECT_DIR/scripts/threads_dashboard_server.py --host $HOST --port $PORT --queue-file $QUEUE_FILE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "Installed dashboard service: $SERVICE_NAME"
echo "Listening on http://$HOST:$PORT"
