# Setup — Debian inside Termux

The recommended path. `proot-distro` Debian gives you glibc, so pip wheels
build without a fight, and Chromium comes straight from apt.

If you want native Termux instead, see [SETUP-TERMUX.md](SETUP-TERMUX.md).

---

## Already have Debian and Chromium? Start here

Skip steps 1 and 2 entirely. Inside Debian, in the cloned repo:

```bash
git pull

apt-get install -y python3-venv python3-full
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt

which chromium          # note the path
cp .env.example .env
nano .env
```

Add to `.env`, alongside the three required values:

```ini
BROWSER_MODE=launch
CHROME_PATH=/usr/bin/chromium     # whatever `which chromium` printed
```

Then jump to [Step 5 — Verify](#step-5--verify-one-check-at-a-time).

The rest of this document is the from-scratch path.

---

## Step 0 — Two values from Telegram, before anything else

The bot **refuses to start** if it does not know who is allowed to command it.
That is deliberate, and it means you cannot discover your own user id by
messaging the bot. Get both of these first:

| Value | Where |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Chat **@BotFather** → `/newbot` → follow the prompts |
| `TELEGRAM_ALLOWED_USERS` | Chat **@userinfobot** → `/start` → copy the numeric id |

---

## Step 1 — Enter Debian

In Termux:

```bash
pkg install -y proot-distro
proot-distro install debian     # skip if already installed
proot-distro login debian
```

Everything from here runs **inside Debian**. Your prompt will look like
`root@localhost:~#`.

---

## Step 2 — Clone and bootstrap

```bash
apt-get update && apt-get install -y git
git clone https://github.com/sixdevilxd/termux-multi-agent
cd termux-multi-agent
bash scripts/setup_debian.sh
```

> **Already cloned earlier?** Run `git pull` first. If `scripts/setup_debian.sh`
> only appears after pulling, your clone predated it — that is why `.venv`
> never existed.

The script:

1. repairs `/etc/resolv.conf` if the proot guest started with no DNS
2. installs Python, git, curl and Chromium
3. creates a virtualenv and installs the Python dependencies
4. locates the Chromium binary and writes `BROWSER_MODE=launch` plus
   `CHROME_PATH` into `.env`

It stops with an explicit message if any of that fails.

---

## Step 3 — Activate the virtualenv

```bash
. .venv/bin/activate
```

Your prompt gains a `(.venv)` prefix.

**You must do this in every new Debian session.** Without it `python run.py`
fails on import. If `. .venv/bin/activate` says *No such file or directory*,
step 2 did not complete — re-run it and read the output.

---

## Step 4 — Fill in `.env`

```bash
nano .env
```

Three values are required:

```ini
TELEGRAM_BOT_TOKEN=123456789:AAE...
TELEGRAM_ALLOWED_USERS=123456789
LLM_API_KEY=sk-...
```

Save with `Ctrl+O` → `Enter` → `Ctrl+X`.

Full reference for every other setting: [CONFIGURATION.md](CONFIGURATION.md).

---

## Step 5 — Verify, one check at a time

Run these in order. Each catches a different failure, and each one is faster to
read than a failed `/run`.

```bash
python run.py --check
```
Confirms the config parses and `CHROME_PATH` points at a real binary.

```bash
python run.py --models claude
```
Lists the model ids your gateway actually accepts. If it warns that
`LLM_MODEL` is not in the list, copy the correct id into `.env`.

```bash
python run.py --ping
```
Sends one real completion. Proves key, base URL and model work together.

---

## Step 6 — Start the bot

```bash
python run.py --bot
```

Then, in **Termux** — not Debian — stop Android suspending the process:

```bash
termux-wake-lock
```

### Keeping it alive when you leave the app

```bash
apt-get install -y tmux
tmux new -s agent
# start the bot inside tmux, then detach with:  Ctrl+B  then  D
# come back later with:
tmux attach -t agent
```

---

## Step 7 — Drive it from Telegram

```
/start                       check the bot is up and you are authorised
/run https://example.com     begin a run
/status                      current phase, page and task counts
/reply <token> <answer>      answer an OTP / password gate
/skip <token>                refuse a gate and continue
/stop                        cancel the run
```

**On a site you have not tried before, set `DRY_RUN=true` first.** The agent
reasons through everything and clicks nothing. Read the report, then turn it
off.

---

## Troubleshooting

**`. .venv/bin/activate` → No such file or directory**
The bootstrap never finished. Re-run `bash scripts/setup_debian.sh` and read
the output. Most common cause is a missing `python3-venv`.

**`error: externally-managed-environment` from pip**
Debian 12 blocks system-wide pip installs. Activate the virtualenv first.

**`Temporary failure resolving 'deb.debian.org'`**
The proot guest has no DNS:
```bash
printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /etc/resolv.conf
```

**Chromium exits immediately / the run dies opening the browser**
proot cannot always fork Chromium's zygote process. Add to `.env`:
```ini
BROWSER_ARGS=--single-process,--no-zygote
```
This is the single most common Chromium failure under proot.

**`CHROME_PATH points at ... which does not exist`**
```bash
which chromium || which chromium-browser
```
Put whatever that prints into `CHROME_PATH`.

**`TELEGRAM_ALLOWED_USERS is empty — refusing to start`**
Fail-closed by design. Fill it in from @userinfobot.

**Bot ignores you completely**
The id in `.env` is not yours. Check again with @userinfobot.

**`bot-protection challenge instead of the API`**
AgentRouter's WAF blocked a datacenter/VPN address. Turn off any VPN, or use
the mirror: `LLM_BASE_URL=ps.air-outer.com`.

**The run stops when the screen locks**
`termux-wake-lock`, in Termux.
