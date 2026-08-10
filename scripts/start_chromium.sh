#!/data/data/com.termux/files/usr/bin/bash
# Start Chromium with a CDP endpoint the agent can attach to.
# Playwright cannot download a browser on Android, so we bring our own.
set -e

PORT="${CDP_PORT:-9222}"
PROFILE="${CDP_PROFILE:-$HOME/.cache/agent-chromium}"

mkdir -p "$PROFILE"

echo "Starting Chromium with CDP on port $PORT"
echo "Profile: $PROFILE"

exec chromium \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins="*" \
  --user-data-dir="$PROFILE" \
  --window-size=412,915 \
  about:blank
