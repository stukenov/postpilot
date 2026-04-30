# Deployment Guide

## Server Requirements

- Any Linux VPS (Ubuntu 22.04+ recommended, $5/month is enough)
- Python 3.11+
- Node.js 18+
- systemd

## Architecture

- **Systemd service**: runs the publisher daemon 24/7 with auto-restart
- **Playwright browsers**: headless Chromium installed on the server
- **State directory**: queues, auth sessions, locks, and debug logs
- **Cron**: optional automatic token refresh

## Step-by-Step Deployment

### 1. Sync files to your server

```bash
rsync -avz \
  --exclude='node_modules' \
  --exclude='.playwright-meta-profile' \
  --exclude='.DS_Store' \
  --exclude='__pycache__' \
  --exclude='.git' \
  ./ your-server:/opt/postpilot/
```

### 2. Install dependencies on the server

```bash
ssh your-server 'cd /opt/postpilot && npm install && npx playwright install chromium'
```

### 3. Fix file ownership

After rsync, state files may have your local UID. Fix ownership for the service user:

```bash
ssh your-server 'chown -R postpilot:postpilot /opt/postpilot/state/'
```

### 4. Build the publish queue

```bash
ssh your-server 'cd /opt/postpilot && python3 cli.py build-queue threads \
  --content-root content/threads/week-YYYY-MM-DD-to-YYYY-MM-DD'
```

### 5. Install and start the systemd service

```bash
ssh your-server 'cd /opt/postpilot && sudo bash scripts/install_threads_publisher_service.sh'
```

### 6. Verify

```bash
ssh your-server 'systemctl status threads-publisher --no-pager'
ssh your-server 'journalctl -u threads-publisher --no-pager -n 20'
```

## Quick Deploy (One Command)

```bash
rsync -avz --exclude='node_modules' --exclude='.git' --exclude='__pycache__' \
  ./ your-server:/opt/postpilot/ \
  && ssh your-server 'chown -R postpilot:postpilot /opt/postpilot/state/ \
  && systemctl restart threads-publisher'
```

## Important Notes

- `.env` on the server must have correct platform credentials and `THREADS_PUBLISH_BACKEND=playwright`
- After rsync, always fix `state/` ownership — otherwise the service can't write queue/lock files
- `build-queue` preserves existing statuses (`posted`, `failed`, `interrupted`, `missed`)
- Stale `publishing` entries are automatically reclassified to `interrupted` after a process restart
- Overdue `pending` items are auto-marked `missed` if their lag exceeds `THREADS_MAX_PUBLISH_LAG_SECONDS`
- The systemd service has `Restart=always` — if it crashes, systemd restarts it in 10 seconds
- Avoid restarting the service during an active publishing slot — check `journalctl` first
- Debug trail: `state/threads-publisher-debug.jsonl` and `state/publish-debug/<date>/<attempt-id>/`

## Browser Re-Authentication

If the browser session expires, re-export the storage state:

```bash
# On a machine with a display (or with X forwarding)
node scripts/threads_export_storage_state.mjs

# Copy the session to the server
scp state/threads-browser-auth.json your-server:/opt/postpilot/state/
```

## Token Auto-Refresh (Optional)

For platforms that support OAuth token refresh (Threads):

```bash
# Install the cron job
ssh your-server 'cd /opt/postpilot && sudo bash scripts/install_threads_token_refresh_cron.sh'

# Manual one-time refresh
ssh your-server 'cd /opt/postpilot && bash scripts/threads_refresh_token.sh'

# Audit current token/cron state
ssh your-server 'cd /opt/postpilot && python3 scripts/threads_token_refresh_audit.py'
```
