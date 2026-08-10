# Setup — native Termux

Runs everything directly in Termux, with no Debian guest. Lighter, but pip has
to build against Termux's libc, so expect the occasional compile.

If you already have `proot-distro` Debian, use
[SETUP-DEBIAN.md](SETUP-DEBIAN.md) instead — it is the smoother path.

> Install Termux from **F-Droid**, not the Play Store. The Play Store build is
> abandoned and its `pkg` is broken.

---

## Step 0 — Two values from Telegram, before anything else

The bot refuses to start until it knows who may command it, so you cannot
discover your own id by messaging it.

| Value | Where |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Chat **@BotFather** → `/newbot` |
| `TELEGRAM_ALLOWED_USERS` | Chat **@userinfobot** → `/start` |

---

## Step 1 — Install

```bash
pkg install -y git
git clone https://github.com/sixdevilxd/termux-multi-agent
cd termux-multi-agent
bash setup_termux.sh
```

This installs Python and build tools, enables `tur-repo`, installs Chromium,
installs the Python dependencies and copies `.env.example` to `.env`.

---

## Step 2 — Fill in `.env`

```bash
nano .env
```

```ini
TELEGRAM_BOT_TOKEN=123456789:AAE...
TELEGRAM_ALLOWED_USERS=123456789
LLM_API_KEY=sk-...
```

Full reference: [CONFIGURATION.md](CONFIGURATION.md).

---

## Step 3 — Start Chromium with a CDP port

Playwright cannot download a browser for Android, so we bring our own and
attach to it. This is why native Termux needs **two** processes.

```bash
pkg install -y tmux
termux-wake-lock
tmux new -s agent

# inside tmux
./scripts/start_chromium.sh &
```

Confirm it is really listening before going further:

```bash
curl -s http://127.0.0.1:9222/json/version
```

You want JSON containing `"Browser": "Chrome/..."`. `Connection refused` means
Chromium did not start — see troubleshooting.

---

## Step 4 — Verify, one check at a time

```bash
python run.py --check             # config parses
python run.py --models claude     # exact model ids the gateway accepts
python run.py --ping              # one real completion
```

---

## Step 5 — Start the bot

```bash
python run.py --bot
```

Detach from tmux with `Ctrl+B` then `D`. Return with `tmux attach -t agent`.

---

## Step 6 — Drive it from Telegram

```
/start                       check the bot is up and you are authorised
/run https://example.com     begin a run
/status                      current phase, page and task counts
/reply <token> <answer>      answer an OTP / password gate
/skip <token>                refuse a gate and continue
/stop                        cancel the run
```

Set `DRY_RUN=true` for the first run on any new site.

---

## Stopping

```bash
tmux attach -t agent
Ctrl+C              # stop the bot
pkill chromium      # stop the browser
termux-wake-unlock
```

---

## Troubleshooting

**`pkg install chromium` fails**
`tur-repo` has no build for your architecture. Two options: use
[Debian in proot](SETUP-DEBIAN.md), or run Chromium on a VPS and point
`CDP_URL=http://your-vps:9222` at it.

**`connect_over_cdp` refused**
Chromium is not running or not on that port. Re-check
`curl http://127.0.0.1:9222/json/version`.

**`greenlet` fails to build during pip install**
```bash
pkg install -y build-essential python-dev binutils
pip install -r requirements.txt
```

**`TELEGRAM_ALLOWED_USERS is empty — refusing to start`**
Fail-closed by design. Fill it in from @userinfobot.

**The run stops when the screen locks**
`termux-wake-lock`.
