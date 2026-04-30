#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
STATE_DIR="${STATE_DIR:-$PROJECT_DIR/state}"
LOCK_FILE="${LOCK_FILE:-$STATE_DIR/threads-token-refresh.lock}"
SUMMARY_FILE="${SUMMARY_FILE:-$STATE_DIR/threads-token-refresh-last.json}"
WHOAMI_FILE="${WHOAMI_FILE:-$STATE_DIR/threads-token-refresh-whoami.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MIN_AGE_SECONDS="${MIN_AGE_SECONDS:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      cat <<EOF
Usage:
  bash scripts/threads_refresh_token.sh [options]

Options:
  --project-dir <path>
  --env-file <path>
  --state-dir <path>
  --summary-file <path>
  --whoami-file <path>
  --python-bin <path>
  --min-age-seconds <n>
EOF
      exit 0
      ;;
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --state-dir)
      STATE_DIR="$2"
      shift 2
      ;;
    --summary-file)
      SUMMARY_FILE="$2"
      shift 2
      ;;
    --whoami-file)
      WHOAMI_FILE="$2"
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

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$STATE_DIR" "$(dirname "$SUMMARY_FILE")" "$(dirname "$WHOAMI_FILE")"
cd "$PROJECT_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Threads token refresh skipped: lock busy"
  exit 0
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${THREADS_ACCESS_TOKEN:-}" ]]; then
  echo "THREADS_ACCESS_TOKEN is empty in $ENV_FILE" >&2
  exit 1
fi

if [[ -z "${MIN_AGE_SECONDS:-}" ]]; then
  MIN_AGE_SECONDS="${THREADS_TOKEN_REFRESH_MIN_AGE_SECONDS:-86400}"
fi

NOW_EPOCH="$(date -u +%s)"
NOW_ISO="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
PREVIOUS_REFRESHED_AT="${THREADS_ACCESS_TOKEN_REFRESHED_AT:-}"
AGE_SECONDS=""

if [[ -n "$PREVIOUS_REFRESHED_AT" ]]; then
  PREVIOUS_REFRESHED_EPOCH="$(date -u -d "$PREVIOUS_REFRESHED_AT" +%s 2>/dev/null || true)"
  if [[ -n "$PREVIOUS_REFRESHED_EPOCH" ]]; then
    AGE_SECONDS="$((NOW_EPOCH - PREVIOUS_REFRESHED_EPOCH))"
  fi
fi

if [[ -n "$AGE_SECONDS" ]] && (( AGE_SECONDS < MIN_AGE_SECONDS )); then
  cat >"$SUMMARY_FILE" <<EOF
{
  "status": "skipped",
  "reason": "min_age_not_reached",
  "checked_at": "$NOW_ISO",
  "last_refreshed_at": "$PREVIOUS_REFRESHED_AT",
  "age_seconds": $AGE_SECONDS,
  "min_age_seconds": $MIN_AGE_SECONDS
}
EOF
  echo "Threads token refresh skipped: age ${AGE_SECONDS}s < ${MIN_AGE_SECONDS}s"
  exit 0
fi

if ! "$PYTHON_BIN" scripts/threads_publisher.py refresh-token --write-env >/dev/null; then
  cat >"$SUMMARY_FILE" <<EOF
{
  "status": "error",
  "checked_at": "$NOW_ISO",
  "last_refreshed_at": "$PREVIOUS_REFRESHED_AT",
  "age_seconds": ${AGE_SECONDS:-null},
  "min_age_seconds": $MIN_AGE_SECONDS
}
EOF
  echo "Threads token refresh failed" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

"$PYTHON_BIN" scripts/threads_publisher.py whoami >"$WHOAMI_FILE"

UPDATED_AT="${THREADS_ACCESS_TOKEN_REFRESHED_AT:-}"
UPDATED_EXPIRES_IN="${THREADS_ACCESS_TOKEN_EXPIRES_IN:-}"
UPDATED_NOW_ISO="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

cat >"$SUMMARY_FILE" <<EOF
{
  "status": "refreshed",
  "checked_at": "$UPDATED_NOW_ISO",
  "previous_refreshed_at": "${PREVIOUS_REFRESHED_AT:-}",
  "refreshed_at": "$UPDATED_AT",
  "expires_in": "${UPDATED_EXPIRES_IN:-}",
  "age_seconds_before_refresh": ${AGE_SECONDS:-null},
  "min_age_seconds": $MIN_AGE_SECONDS,
  "whoami_file": "$WHOAMI_FILE"
}
EOF

echo "Threads token refreshed: $UPDATED_AT"
