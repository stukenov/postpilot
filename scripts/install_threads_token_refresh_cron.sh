#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUN_USER="${RUN_USER:-threads}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/threads-token-refresh}"
LOG_FILE="${LOG_FILE:-$PROJECT_DIR/state/threads-token-refresh.log}"
SCHEDULE="${SCHEDULE:-17 * * * *}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
MIN_AGE_SECONDS="${MIN_AGE_SECONDS:-86400}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      cat <<EOF
Usage:
  sudo bash scripts/install_threads_token_refresh_cron.sh [options]

Options:
  --project-dir <path>
  --run-user <user>
  --cron-file <path>
  --log-file <path>
  --schedule "<cron expr>"
  --python-bin <path>
  --min-age-seconds <n>
EOF
      exit 0
      ;;
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --cron-file)
      CRON_FILE="$2"
      shift 2
      ;;
    --log-file)
      LOG_FILE="$2"
      shift 2
      ;;
    --schedule)
      SCHEDULE="$2"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --min-age-seconds)
      MIN_AGE_SECONDS="$2"
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

mkdir -p "$(dirname "$CRON_FILE")" "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"
chown "$RUN_USER:$RUN_USER" "$LOG_FILE"

cat >"$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

$SCHEDULE $RUN_USER cd $PROJECT_DIR && PROJECT_DIR=$PROJECT_DIR PYTHON_BIN=$PYTHON_BIN MIN_AGE_SECONDS=$MIN_AGE_SECONDS bash scripts/threads_refresh_token.sh >> $LOG_FILE 2>&1
EOF

chmod 0644 "$CRON_FILE"
systemctl enable --now cron >/dev/null 2>&1 || true

echo "Installed Threads token refresh cron: $CRON_FILE"
echo "Schedule: $SCHEDULE"
echo "Run user: $RUN_USER"
echo "Log file: $LOG_FILE"
echo "Min age seconds: $MIN_AGE_SECONDS"
