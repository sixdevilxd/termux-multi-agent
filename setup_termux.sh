#!/data/data/com.termux/files/usr/bin/bash
# One-shot Termux bootstrap for termux-multi-agent.
set -e

echo "==> Updating Termux packages"
pkg update -y && pkg upgrade -y

echo "==> Installing base tooling"
pkg install -y python git build-essential binutils libjpeg-turbo libxml2 libxslt

echo "==> Enabling tur-repo (provides chromium on aarch64)"
pkg install -y tur-repo || echo "!! tur-repo unavailable; you will need remote CDP instead"

echo "==> Installing Chromium (optional but recommended)"
pkg install -y chromium || echo "!! chromium install failed - set CDP_URL to a remote browser"

echo "==> Installing Python dependencies"
pip install --upgrade pip wheel
pip install -r requirements.txt

echo "==> Preparing config"
[ -f .env ] || cp .env.example .env

cat <<'EOF'

Done.

Next steps:
  1. nano .env                 # add TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, LLM_API_KEY
  2. ./scripts/start_chromium.sh &     # start the browser with a CDP port open
  3. python run.py --check     # verify configuration
  4. python run.py --bot       # start the Telegram bot

Tip: run `termux-wake-lock` so Android does not suspend long runs.
EOF
