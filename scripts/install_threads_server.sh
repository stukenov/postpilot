#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_NAME="${SERVICE_NAME:-threads-publisher}"
RUN_USER="${RUN_USER:-threads}"
DOMAIN="${DOMAIN:-your-domain.com}"
CONTENT_ROOT="${CONTENT_ROOT:-$PROJECT_DIR/content/threads-week-2026-03-10-to-2026-03-16}"
QUEUE_FILE="${QUEUE_FILE:-$PROJECT_DIR/state/threads-publish-queue.json}"
TIMEZONE_NAME="${TIMEZONE_NAME:-Asia/Almaty}"
CALLBACK_BIND_PORT="${CALLBACK_BIND_PORT:-8787}"
CADDY_IMPORT_DIR="${CADDY_IMPORT_DIR:-/etc/caddy/conf.d}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      cat <<EOF
Usage:
  sudo bash scripts/install_threads_server.sh [options]

Options:
  --domain <fqdn>
  --project-dir <path>
  --service-name <name>
  --run-user <user>
  --content-root <path>
  --queue-file <path>
  --timezone <iana-timezone>
  --callback-bind-port <port>
EOF
      exit 0
      ;;
    --domain)
      DOMAIN="$2"
      shift 2
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
    --content-root)
      CONTENT_ROOT="$2"
      shift 2
      ;;
    --queue-file)
      QUEUE_FILE="$2"
      shift 2
      ;;
    --timezone)
      TIMEZONE_NAME="$2"
      shift 2
      ;;
    --callback-bind-port)
      CALLBACK_BIND_PORT="$2"
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

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y python3 python3-venv curl rsync caddy nodejs npm

if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$PROJECT_DIR" --shell /usr/sbin/nologin "$RUN_USER"
fi

mkdir -p "$PROJECT_DIR" "$(dirname "$QUEUE_FILE")" "$CADDY_IMPORT_DIR"
chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR" "$(dirname "$QUEUE_FILE")"

pushd "$PROJECT_DIR" >/dev/null
npm ci --omit=dev
PLAYWRIGHT_BROWSERS_PATH="$PROJECT_DIR/.playwright-browsers" npx playwright install --with-deps chromium
popd >/dev/null
chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR"

sudo -u "$RUN_USER" python3 "$PROJECT_DIR/scripts/threads_publisher.py" export-txt --content-root "$CONTENT_ROOT"
sudo -u "$RUN_USER" python3 "$PROJECT_DIR/scripts/threads_publisher.py" build-queue \
  --content-root "$CONTENT_ROOT" \
  --queue-file "$QUEUE_FILE" \
  --timezone "$TIMEZONE_NAME"

SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=Threads Publisher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
Environment=PYTHONUNBUFFERED=1
Environment=PLAYWRIGHT_BROWSERS_PATH=$PROJECT_DIR/.playwright-browsers
ExecStart=/usr/bin/env python3 $PROJECT_DIR/scripts/threads_publisher.py run --queue-file $QUEUE_FILE
KillMode=process
TimeoutStopSec=180
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/caddy
if [[ ! -f /etc/caddy/Caddyfile ]]; then
  cat >/etc/caddy/Caddyfile <<'EOF'
{
}
EOF
fi

if ! grep -qF "import $CADDY_IMPORT_DIR/*.caddy" /etc/caddy/Caddyfile; then
  printf "\nimport %s/*.caddy\n" "$CADDY_IMPORT_DIR" >> /etc/caddy/Caddyfile
fi

CADDY_FILE="$CADDY_IMPORT_DIR/$SERVICE_NAME.caddy"
cat >"$CADDY_FILE" <<EOF
$DOMAIN {
    encode gzip

    @callback path /callback /callback*
    handle @callback {
        reverse_proxy 127.0.0.1:$CALLBACK_BIND_PORT
    }

    @privacy path /privacy-policy /privacy-policy/
    handle @privacy {
        root * $PROJECT_DIR/public
        rewrite * /privacy-policy.html
        file_server
    }

    handle {
        respond "threads publisher callback ready" 200
    }
}
EOF

systemctl daemon-reload
systemctl enable caddy
systemctl restart caddy

SHOULD_START=0
if grep -qE '^THREADS_PUBLISH_BACKEND=playwright$' "$PROJECT_DIR/.env"; then
  STORAGE_STATE_FILE="$(grep '^THREADS_PLAYWRIGHT_STORAGE_STATE=' "$PROJECT_DIR/.env" | cut -d= -f2- || true)"
  if [[ -n "$STORAGE_STATE_FILE" && "$STORAGE_STATE_FILE" != /* ]]; then
    STORAGE_STATE_FILE="$PROJECT_DIR/$STORAGE_STATE_FILE"
  fi
  if [[ -n "$STORAGE_STATE_FILE" && -f "$STORAGE_STATE_FILE" ]]; then
    SHOULD_START=1
  fi
elif grep -qE '^THREADS_ACCESS_TOKEN=.+$' "$PROJECT_DIR/.env"; then
  SHOULD_START=1
fi

if [[ "$SHOULD_START" -eq 1 ]]; then
  systemctl enable --now "$SERVICE_NAME"
  echo "Service enabled and started: $SERVICE_NAME"
else
  echo "Service file created, but not started because publisher auth is incomplete."
  echo "After auth is ready, run:"
  echo "  systemctl enable --now $SERVICE_NAME"
fi

echo "Caddy configured for: https://$DOMAIN"
echo "Publisher service file: $SERVICE_FILE"
