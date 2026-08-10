# termux-multi-agent

A multi-agent web automation system that runs **entirely from your phone** — Termux for compute, Telegram for control, GitHub for the code.

You send a URL. A swarm of specialised agents opens the site, logs in (asking you for the OTP when it hits one), explores the app, works out what activities exist, does them, verifies the result, and sends you a report.

```
USER ──url──> ORCHESTRATOR
                   │
                   ▼
            BROWSER AGENT  (Chromium over CDP)
                   │
                   ▼
            LOGIN DETECTOR ──── form · one-click · already-authenticated
                   │
             OTP or CAPTCHA? ──► HUMAN GATE (Telegram)
                   │
                   ▼
        SITE UNDERSTANDING  "what is this product, and what does
                   │         it call its rewards?"
                   ▼
            DISCOVERY AGENT  steered by the learned vocabulary,
                   │         re-ranked by the model as it goes
                   ▼
             TASK MINER (AI)  classify · score · prioritise
                   │
                   ▼
          ┌──► TASK PLANNER ──► BROWSER AGENT ──► VERIFIER ──┐
          │                                                  │
          └──────────── retry with hint ◄────────────────────┘
                   │
                   ▼
                REPORT (markdown + Telegram)
```

The agent is given one standing goal:

> *Understand this website and identify legitimate activities that can generate
> points, XP, badges, rewards, or campaign progress — even when those
> activities are not explicitly labelled as tasks, quests, or missions.*

---

## Why this design

| Decision | Reason |
| --- | --- |
| **Understand the site before crawling it** | A hardcoded keyword list (`quest`, `reward`, ...) silently misses any site that calls its currency "Sparks" or writes its UI in Indonesian. The agent learns the site's own vocabulary first, then explores with it. |
| **Credentials never reach the model** | Secrets live in a vault and travel as `{{secret:s1}}` tokens. Only the browser layer resolves them, in the statement before typing. Every prompt is scrubbed on the way out. |
| **Reward counters are the primary success signal** | "The button was clicked" ≠ "the task completed". If `Sparks` went from 1,240 to 1,290, it worked — no model opinion required. |
| **Plan 1-4 actions at a time**, not a full script | SPAs mutate under you. A 20-step plan written up front is fiction. |
| **Indexed DOM snapshot**, not raw HTML | Raw HTML destroys the context window. We send only what is clickable, plus a text digest. |
| **Verifier can send a task back to the planner** | Failure carries a retry hint, so the loop is a loop and not a straight line. |
| **Guardrails run before every action** | Domain lock + destructive-intent filter + hard action budget. An autonomous loop needs a brake. |
| **OTP/CAPTCHA suspend on an `asyncio.Future`** | The pipeline pauses exactly where it stopped and resumes on your Telegram reply. No polling, no restart. |
| **Session saved per domain** | Log in once. Later runs restore cookies and skip straight to understanding. |
| **CDP mode by default** | Playwright cannot ship a Chromium binary for Android. We attach to one Termux installs itself. |

---

### Already running Debian in Termux?

`proot-distro` Debian is the better host: glibc means pip wheels build without a
fight, and proot shares Termux's network stack, so `127.0.0.1` is the same
localhost on both sides. Use the Debian bootstrap instead of `setup_termux.sh`:

```bash
# in Termux
pkg install -y proot-distro
proot-distro login debian

# now inside Debian
apt-get update && apt-get install -y git
git clone https://github.com/sixdevilxd/termux-multi-agent
cd termux-multi-agent
bash scripts/setup_debian.sh
```

That script installs Chromium, builds a virtualenv, finds the Chromium binary
and writes `BROWSER_MODE=launch` + `CHROME_PATH` into `.env` for you — so
Playwright drives the system browser directly and there is **no separate CDP
process to start or babysit**.

```bash
. .venv/bin/activate     # every new Debian session
nano .env                # the three required values
python run.py --check    # confirms CHROME_PATH resolves
python run.py --bot
```

Run `termux-wake-lock` in **Termux**, not in Debian.

---

## Quick start (Termux)

```bash
pkg install -y git
git clone https://github.com/sixdevilxd/termux-multi-agent
cd termux-multi-agent
bash setup_termux.sh
```

Then edit `.env`:

```bash
nano .env
```

Minimum you must fill in:

```ini
TELEGRAM_BOT_TOKEN=...        # from @BotFather
TELEGRAM_ALLOWED_USERS=...    # your numeric id, from @userinfobot

LLM_PROVIDER=agentrouter
LLM_BASE_URL=agentrouter.org  # just the host — the API path is derived
LLM_MODEL=claude-opus-5
LLM_API_KEY=sk-...            # from https://agentrouter.org/console
```

Confirm the key and model actually work before your first run:

```bash
python run.py --models claude   # list the exact model ids the gateway accepts
python run.py --ping            # send one test completion
```

Start the browser, then the bot:

```bash
termux-wake-lock                  # stop Android suspending long runs
./scripts/start_chromium.sh &     # Chromium with CDP on :9222
python run.py --check             # sanity-check the config
python run.py --bot               # start the Telegram bot
```

In Telegram:

```
/run https://example.com
```

### Panduan singkat (Bahasa Indonesia)

1. `bash setup_termux.sh` — pasang semua dependensi.
2. `nano .env` — isi token Telegram, user ID kamu, dan API key LLM.
3. `./scripts/start_chromium.sh &` — jalankan Chromium dengan port CDP.
4. `python run.py --bot` — bot aktif, lalu kirim `/run <url>` di Telegram.
5. Kalau kena OTP atau CAPTCHA, bot akan bertanya. Balas dengan `/reply <token> <jawaban>`.

---

## Login is adaptive

The agent does not assume every site behaves the same way.

| Case | What happens |
| --- | --- |
| **Already authenticated** | A saved session is restored; login is skipped entirely. |
| **Email + password** | Fields are located by type and metadata, filled from the vault, submitted, then verified. |
| **One-click sign-in** | "Continue with Google", "Sign in with X", wallet buttons — detected and followed. |
| **OTP** | Run pauses. Telegram asks you for the code. You reply. Run resumes. |
| **CAPTCHA** | Run pauses. You solve it. The agent detects authentication success and continues. |

### Credentials never reach the AI model

This is enforced by the architecture, not by convention:

1. The human gate collects the secret and puts it straight into an in-memory
   vault, which returns an opaque token: `{{secret:s1}}`.
2. The planner, the run state, the logs and the report only ever see that token.
3. `BrowserAgent` resolves the token in the statement immediately before
   `locator.fill()` — the browser layer is the only place plaintext exists.
4. Every prompt is passed through `vault.redact()` before egress, so a secret
   that somehow lands in a page digest is scrubbed anyway.
5. A field detected as sensitive (`password`, `otp`, `pin`, `cvv`, `token`, ...)
   **can only be filled from a vault token**. If a planner ever invents a
   password, the action is refused rather than typed.
6. The DOM snapshot never reads the value of a sensitive input in the first
   place.
7. The vault is cleared when the run ends.

Sessions are persisted locally per domain in `storage/sessions/`, mode `0600`,
gitignored — so you log in once, not once per run.

---

## Telegram commands

| Command | Effect |
| --- | --- |
| `/run <url>` | Start a run |
| `/status` | Current phase, page/task counts, open gate |
| `/reply <token> <answer>` | Answer a human gate (OTP, password, ...) |
| `/skip <token>` | Refuse a gate, let the run continue |
| `/stop` | Cancel the running pipeline |
| `/report` | Resend the last summary |

A plain text message (no command) answers the oldest open gate — faster than typing tokens on a phone. Messages containing secrets are deleted from the chat after they are consumed.

---

## Running without Termux

Any Linux box works. Set `BROWSER_MODE=launch` and Playwright manages its own browser:

```bash
pip install -r requirements.txt
playwright install chromium
BROWSER_MODE=launch python run.py --url https://example.com
```

`--url` runs once and prints the report. Interactive gates need `--bot`.

---

## Configuration

Every knob lives in `.env` (see `.env.example`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `agentrouter` | `agentrouter` · `openai` · `anthropic` · `gemini` · `openrouter` |
| `LLM_MODEL` | `claude-opus-5` | Exact gateway model id — check with `--models` |
| `LLM_BASE_URL` | *(provider default)* | Host only — scheme and API path are added for you |
| `BROWSER_MODE` | `cdp` | `cdp` attaches to a running Chromium; `launch` lets Playwright start one |
| `CDP_URL` | `http://127.0.0.1:9222` | Where the browser is listening (`cdp` mode) |
| `CHROME_PATH` | *(empty)* | System Chromium binary for `launch` mode — required on Android/proot |
| `BROWSER_ARGS` | *(empty)* | Extra Chromium flags, comma separated |
| `DRY_RUN` | `false` | Plan everything, click nothing |
| `MAX_ACTIONS` | `120` | Hard ceiling on browser actions per run |
| `MAX_DISCOVERY_PAGES` | `25` | Crawl budget |
| `HUMAN_GATE_TIMEOUT` | `600` | Seconds a gate waits for you |

### Providers

| Provider | Host | Request path | Wire format |
| --- | --- | --- | --- |
| `agentrouter` | `agentrouter.org` | `/v1/chat/completions` | OpenAI |
| `openai` | `api.openai.com` | `/v1/chat/completions` | OpenAI |
| `openrouter` | `openrouter.ai/api` | `/v1/chat/completions` | OpenAI |
| `anthropic` | `api.anthropic.com` | `/v1/messages` | Anthropic |
| `gemini` | `generativelanguage.googleapis.com` | `/v1beta/models/...` | Gemini |

**You only ever configure the host.** `LLM_BASE_URL=agentrouter.org`,
`https://agentrouter.org` and `https://agentrouter.org/v1` all resolve to the
same API root — the scheme is added if missing and `/v1` is appended only when
it is not already there, so it can never be doubled.

AgentRouter serves both wire formats from that one host:

```ini
LLM_PROVIDER=agentrouter   # -> POST agentrouter.org/v1/chat/completions
LLM_PROVIDER=anthropic     # -> POST agentrouter.org/v1/messages
LLM_BASE_URL=agentrouter.org
```

Either reaches `claude-opus-5`. Start with `agentrouter`; switch to
`anthropic` only if you specifically want the native Claude message format.
Mainland China mirror: `LLM_BASE_URL=ps.air-outer.com`.

Model ids differ between gateways (`claude-opus-5` here vs
`anthropic/claude-opus-5` on OpenRouter). `python run.py --models` prints the
authoritative list for whichever gateway you configured and flags your
`LLM_MODEL` if it is not on it.

---

## Project layout

```
config/settings.py     typed config, loaded once
core/bus.py            async event bus (agents → Telegram/CLI)
core/state.py          RunState, persisted after every phase
core/llm.py            provider-agnostic LLM client, scrubs every prompt
core/secrets.py        credential vault — tokens out, plaintext never
core/rewards.py        reward-counter parser built from learned vocabulary
core/guardrails.py     domain lock · intent filter · action budget
browser/driver.py      Playwright lifecycle, CDP attach
browser/dom.py         indexed snapshot; never reads secret field values
browser/session.py     per-domain cookie persistence
agents/                base · browser · login · site_understanding
                       · discovery · task_miner · planner · verifier
                       · reporter · orchestrator
tgbot/bot.py           Telegram commands
tgbot/human_gate.py    OTP/CAPTCHA suspension
tests/                 offline suite — no browser, network or API key needed
```

---

## Troubleshooting

**`bot-protection challenge instead of the API`**
AgentRouter sits behind an Aliyun WAF that serves an HTML challenge to
datacenter and VPN addresses. Run from a normal mobile or home connection, or
switch `LLM_BASE_URL` to the mirror `https://ps.air-outer.com/v1`.

**`HTTP 401` / `403`**
Bad or expired `LLM_API_KEY`. Regenerate it at the provider console.

**`HTTP 404` on `/chat/completions`**
The gateway does not serve the OpenAI wire format at that host. Check
`python run.py --check` — it prints the exact `api root` being called — or try
`LLM_PROVIDER=anthropic` against the same host.

**`LLM_MODEL ... is not in this list`**
Model ids are gateway-specific. Copy an exact id from `python run.py --models`.

**`connect_over_cdp` refused**
Chromium is not running, or not listening on `CDP_URL`. Start it with
`./scripts/start_chromium.sh &` and confirm with `curl http://127.0.0.1:9222/json/version`.
In Debian/proot you can skip CDP entirely: set `BROWSER_MODE=launch` and
`CHROME_PATH=/usr/bin/chromium`.

**Chromium exits immediately under proot**
proot cannot always fork the zygote process. Add
`BROWSER_ARGS=--single-process,--no-zygote` to `.env`.

**`Temporary failure resolving` inside Debian**
The proot guest started with an empty `/etc/resolv.conf`. Write
`nameserver 8.8.8.8` into it (`scripts/setup_debian.sh` does this automatically).

**`error: externally-managed-environment` from pip in Debian**
Debian 12 blocks system-wide pip. Use the virtualenv:
`python3 -m venv .venv && . .venv/bin/activate`.

**Run dies when the screen locks**
Android suspended Termux. Run `termux-wake-lock` before starting.

---

## Safety

Guardrails refuse any control whose label matches a destructive, financial, or public-posting pattern (`delete`, `withdraw`, `checkout`, `post`, `hapus`, `bayar`, ...), and any navigation that leaves the target origin. Blocked attempts are recorded in the run report.

Start with `DRY_RUN=true` on a new site. Read the report. Then turn it off.

> **Use this only on sites you own or are permitted to automate.** Automating quests, XP, or reward systems usually violates a site's terms of service. That is your call to make, and your responsibility.

Sessions in `storage/sessions/` contain live auth cookies. They are gitignored and written `0600`. Do not commit them.

---

## Roadmap

- [ ] Resume an interrupted run from its saved `RunState`
- [ ] Vision fallback when the DOM snapshot is not enough
- [ ] Per-site plugins for known task patterns
- [ ] Scheduled runs (daily check-ins) via cron + `termux-job-scheduler`

---

## License

MIT — see [LICENSE](LICENSE).
