#!/bin/bash
# Bootstrap inside a proot-distro Debian guest on Termux.
#
# Run this AFTER:  proot-distro login debian
# Not interchangeable with setup_termux.sh — that one targets Termux itself.
set -e

echo "==> Sanity check: are we actually in Debian?"
if [ ! -f /etc/debian_version ]; then
  echo "!! This is not a Debian guest. Run 'proot-distro login debian' first." >&2
  exit 1
fi
echo "    Debian $(cat /etc/debian_version)"

# proot guests frequently start with an empty resolv.conf, which makes apt
# fail with confusing "Temporary failure resolving" errors.
if ! grep -q nameserver /etc/resolv.conf 2>/dev/null; then
  echo "==> Repairing DNS (/etc/resolv.conf was empty)"
  printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /etc/resolv.conf
fi

echo "==> Updating apt"
apt-get update -y && apt-get upgrade -y

echo "==> Installing base packages"
apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv git curl nano ca-certificates \
  chromium fonts-liberation

echo "==> Creating virtualenv (Debian 12 blocks system-wide pip installs)"
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

echo "==> Locating Chromium"
CHROME_BIN="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROME_BIN" ]; then
  echo "!! Chromium not found. Install it, then set CHROME_PATH in .env manually." >&2
else
  echo "    $CHROME_BIN"
fi

echo "==> Preparing config"
if [ ! -f .env ]; then
  cp .env.example .env
  # Point the config at the Chromium we just found, in launch mode: one
  # process, no separate CDP server to babysit.
  if [ -n "$CHROME_BIN" ]; then
    sed -i "s|^BROWSER_MODE=.*|BROWSER_MODE=launch|" .env
    if grep -q '^# *CHROME_PATH=' .env; then
      sed -i "s|^# *CHROME_PATH=.*|CHROME_PATH=$CHROME_BIN|" .env
    elif grep -q '^CHROME_PATH=' .env; then
      sed -i "s|^CHROME_PATH=.*|CHROME_PATH=$CHROME_BIN|" .env
    else
      printf '\nCHROME_PATH=%s\n' "$CHROME_BIN" >> .env
    fi
  fi
fi

cat <<EOF

Done.

Every new Debian session, activate the venv first:
  . .venv/bin/activate

Next steps:
  1. nano .env                 # TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, LLM_API_KEY
  2. python run.py --check     # confirms CHROME_PATH resolves
  3. python run.py --models claude
  4. python run.py --ping
  5. python run.py --bot

If Chromium refuses to start under proot, add this to .env and retry:
  BROWSER_ARGS=--single-process,--no-zygote

Run 'termux-wake-lock' in TERMUX (not in Debian) so Android does not suspend
long runs.
EOF
