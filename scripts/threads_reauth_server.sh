#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
STATE_DIR="${STATE_DIR:-$PROJECT_DIR/state}"
CALLBACK_FILE="${CALLBACK_FILE:-$STATE_DIR/oauth-callback.json}"
TOKEN_RAW_FILE="${TOKEN_RAW_FILE:-$STATE_DIR/threads-reauth-token-raw.json}"
CALLBACK_CAPTURE_TIMEOUT_SECONDS="${CALLBACK_CAPTURE_TIMEOUT_SECONDS:-180}"
THREADS_OAUTH_PROXY_CONTAINER="${THREADS_OAUTH_PROXY_CONTAINER:-caddy-1}"
THREADS_OAUTH_PROXY_CADDYFILE="${THREADS_OAUTH_PROXY_CADDYFILE:-/srv/caddy/Caddyfile}"
THREADS_OAUTH_CALLBACK_IMAGE="${THREADS_OAUTH_CALLBACK_IMAGE:-python:3.12-alpine}"
SKIP_CADDY_PATCH=0
SKIP_REDIRECT_WHITELIST=0
SKIP_SMOKE_TEST=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      cat <<EOF
Usage:
  bash scripts/threads_reauth_server.sh [options]

Options:
  --project-dir <path>
  --env-file <path>
  --proxy-container <docker-container>
  --proxy-caddyfile <path>
  --skip-caddy-patch
  --skip-redirect-whitelist
  --skip-smoke-test
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
    --proxy-container)
      THREADS_OAUTH_PROXY_CONTAINER="$2"
      shift 2
      ;;
    --proxy-caddyfile)
      THREADS_OAUTH_PROXY_CADDYFILE="$2"
      shift 2
      ;;
    --skip-caddy-patch)
      SKIP_CADDY_PATCH=1
      shift
      ;;
    --skip-redirect-whitelist)
      SKIP_REDIRECT_WHITELIST=1
      shift
      ;;
    --skip-smoke-test)
      SKIP_SMOKE_TEST=1
      shift
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

mkdir -p "$STATE_DIR"

set -a
source "$ENV_FILE"
set +a

cd "$PROJECT_DIR"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required env var: $name" >&2
    exit 1
  fi
}

require_cmd() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Missing required command: $name" >&2
    exit 1
  fi
}

require_env THREADS_META_APP_ID
require_env THREADS_APP_ID
require_env THREADS_REDIRECT_URI
require_env THREADS_SCOPES

require_cmd python3
require_cmd docker

THREADS_NODE_BIN="${THREADS_NODE_BIN:-node}"
require_cmd "$THREADS_NODE_BIN"

if [[ -z "${THREADS_PLAYWRIGHT_STORAGE_STATE:-}" && -z "${THREADS_PLAYWRIGHT_PROFILE_DIR:-}" ]]; then
  echo "Set THREADS_PLAYWRIGHT_STORAGE_STATE or THREADS_PLAYWRIGHT_PROFILE_DIR in .env" >&2
  exit 1
fi

REDIRECT_HOST="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.urlparse(sys.argv[1]).hostname or "")' "$THREADS_REDIRECT_URI")"
REDIRECT_PATH="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.urlparse(sys.argv[1]).path or "/")' "$THREADS_REDIRECT_URI")"
CALLBACK_PORT="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.urlparse(sys.argv[1]).port or 8787)' "${THREADS_CALLBACK_BIND_URI:-http://127.0.0.1:8787/callback}")"

if [[ "$SKIP_CADDY_PATCH" -eq 0 && -f "$THREADS_OAUTH_PROXY_CADDYFILE" ]]; then
  python3 - "$THREADS_OAUTH_PROXY_CADDYFILE" "$REDIRECT_HOST" "$REDIRECT_PATH" "$CALLBACK_PORT" <<'PY'
from pathlib import Path
import sys

caddy_path = Path(sys.argv[1])
host = sys.argv[2]
route_path = sys.argv[3] or "/"
port = sys.argv[4]

text = caddy_path.read_text(encoding="utf-8")
block = f"  handle {route_path}* {{\n    reverse_proxy 127.0.0.1:{port}\n  }}\n"
if block in text:
    raise SystemExit(0)

lines = text.splitlines()
host_index = None
for index, line in enumerate(lines):
    stripped = line.strip()
    if stripped.endswith("{") and host in stripped:
        host_index = index
        break

if host_index is None:
    raise SystemExit(0)

depth = 0
insert_at = None
for index in range(host_index, len(lines)):
    depth += lines[index].count("{")
    depth -= lines[index].count("}")
    if index > host_index and depth == 0:
        insert_at = index
        break

if insert_at is None:
    raise SystemExit(0)

lines.insert(insert_at, block.rstrip("\n"))
caddy_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

  docker exec "$THREADS_OAUTH_PROXY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile >/dev/null
fi

LISTENER_SESSION_ID=""
cleanup() {
  if [[ -n "$LISTENER_SESSION_ID" ]]; then
    docker kill "$LISTENER_SESSION_ID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

rm -f "$CALLBACK_FILE"
rm -f "$TOKEN_RAW_FILE"

LISTENER_SESSION_ID="$(
  docker run -d \
    --network "container:$THREADS_OAUTH_PROXY_CONTAINER" \
    -v "$PROJECT_DIR:/work" \
    -w /work \
    "$THREADS_OAUTH_CALLBACK_IMAGE" \
    python scripts/threads_capture_callback.py \
      --host 127.0.0.1 \
      --port "$CALLBACK_PORT" \
      --path "$REDIRECT_PATH" \
      --output "${CALLBACK_FILE#$PROJECT_DIR/}" \
      --timeout-seconds "$CALLBACK_CAPTURE_TIMEOUT_SECONDS"
)"

BROWSER_ARGS=(
  "$PROJECT_DIR/scripts/threads_reauth_playwright.mjs"
  --meta-app-id "$THREADS_META_APP_ID"
  --threads-app-id "$THREADS_APP_ID"
  --redirect-uri "$THREADS_REDIRECT_URI"
  --scopes "$THREADS_SCOPES"
  --screenshot-path "$STATE_DIR/threads-reauth-browser.png"
)

if [[ -n "${THREADS_PLAYWRIGHT_STORAGE_STATE:-}" ]]; then
  BROWSER_ARGS+=(--storage-state "$THREADS_PLAYWRIGHT_STORAGE_STATE")
fi
if [[ -n "${THREADS_PLAYWRIGHT_PROFILE_DIR:-}" ]]; then
  BROWSER_ARGS+=(--profile-dir "$THREADS_PLAYWRIGHT_PROFILE_DIR")
fi
if [[ -n "${THREADS_PLAYWRIGHT_BROWSER_EXECUTABLE:-}" ]]; then
  BROWSER_ARGS+=(--browser-executable "$THREADS_PLAYWRIGHT_BROWSER_EXECUTABLE")
elif [[ -z "${THREADS_PLAYWRIGHT_PROFILE_DIR:-}" ]]; then
  PLAYWRIGHT_CHROME="$(find "$PROJECT_DIR/.playwright-browsers" -path '*chrome-linux64/chrome' -print -quit 2>/dev/null || true)"
  if [[ -n "$PLAYWRIGHT_CHROME" ]]; then
    BROWSER_ARGS+=(--browser-executable "$PLAYWRIGHT_CHROME")
  fi
fi
if [[ -n "${THREADS_PLAYWRIGHT_BROWSER_CHANNEL:-}" ]]; then
  BROWSER_ARGS+=(--browser-channel "$THREADS_PLAYWRIGHT_BROWSER_CHANNEL")
fi
if [[ "${THREADS_PLAYWRIGHT_HEADLESS:-true}" =~ ^(1|true|yes|on)$ ]]; then
  BROWSER_ARGS+=(--headless)
else
  BROWSER_ARGS+=(--headed)
fi

if [[ "$SKIP_REDIRECT_WHITELIST" -eq 1 ]]; then
  BROWSER_ARGS+=(--skip-settings)
fi

PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$PROJECT_DIR/.playwright-browsers}" \
  "$THREADS_NODE_BIN" "${BROWSER_ARGS[@]}" >"$STATE_DIR/threads-reauth-browser.json"

for _ in $(seq 1 "$CALLBACK_CAPTURE_TIMEOUT_SECONDS"); do
  if [[ -f "$CALLBACK_FILE" ]]; then
    break
  fi
  sleep 1
done

if [[ ! -f "$CALLBACK_FILE" ]]; then
  echo "OAuth callback was not captured into $CALLBACK_FILE" >&2
  exit 1
fi

OAUTH_CODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["query"]["code"])' "$CALLBACK_FILE")"

python3 "$PROJECT_DIR/scripts/threads_publisher.py" exchange-full-code --write-env "$OAUTH_CODE" \
  >"$TOKEN_RAW_FILE"

python3 - "$TOKEN_RAW_FILE" "$STATE_DIR/threads-reauth-token.json" "$ENV_FILE" <<'PY'
from pathlib import Path
import json
import sys

raw_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
env_path = Path(sys.argv[3])
raw_text = raw_path.read_text(encoding="utf-8")
json_text = raw_text.split("\n\nSaved Threads credentials and long-lived token into .env", 1)[0]
payload = json.loads(json_text)

refreshed_at = None
for line in env_path.read_text(encoding="utf-8").splitlines():
    if line.startswith("THREADS_ACCESS_TOKEN_REFRESHED_AT="):
        refreshed_at = line.split("=", 1)[1]
        break

summary = {
    "profile": payload.get("profile"),
    "token_type": payload.get("long_lived", {}).get("token_type"),
    "expires_in": payload.get("long_lived", {}).get("expires_in"),
    "refreshed_at": refreshed_at,
}
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
raw_path.unlink(missing_ok=True)
PY

if [[ "$SKIP_SMOKE_TEST" -eq 0 ]]; then
  python3 "$PROJECT_DIR/scripts/threads_publisher.py" whoami >"$STATE_DIR/threads-reauth-whoami.json"
  python3 "$PROJECT_DIR/scripts/threads_publisher.py" user-insights --metrics followers_count \
    >"$STATE_DIR/threads-reauth-user-insights.json"
fi

rm -f "$CALLBACK_FILE"

echo "Threads OAuth reauth complete."
echo "Token written to: $ENV_FILE"
echo "Browser trace: $STATE_DIR/threads-reauth-browser.json"
echo "Token payload: $STATE_DIR/threads-reauth-token.json"
if [[ "$SKIP_SMOKE_TEST" -eq 0 ]]; then
  echo "Whoami: $STATE_DIR/threads-reauth-whoami.json"
  echo "User insights: $STATE_DIR/threads-reauth-user-insights.json"
fi
