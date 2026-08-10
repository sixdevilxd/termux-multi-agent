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
            LOGIN DETECTOR ──── form / one-click
                   │
             OTP or CAPTCHA? ──► HUMAN GATE (Telegram)
                   │
                   ▼
            DISCOVERY AGENT  (BFS site map)
                   │
                   ▼
             TASK MINER (AI)  quiz · quest · check-in · claim
                   │
                   ▼
          ┌──► TASK PLANNER ──► BROWSER AGENT ──► VERIFIER ──┐
          │                                                  │
          └──────────── retry with hint ◄────────────────────┘
                   │
                   ▼
                REPORT (markdown + Telegram)
```

---

## Why this design

| Decision | Reason |
| --- | --- |
| **Plan 1-4 actions at a time**, not a full script | SPAs mutate under you. A 20-step plan written up front is fiction. |
| **Indexed DOM snapshot**, not raw HTML | Raw HTML destroys the context window. We send only what is clickable, plus a text digest. |
| **Verifier can send a task back to the planner** | "The button was clicked" ≠ "the task completed". Failure carries a retry hint. |
| **Guardrails run before every action** | Domain lock + destructive-intent filter + hard action budget. An autonomous loop needs a brake. |
| **OTP/CAPTCHA suspend on an `asyncio.Future`** | The pipeline pauses exactly where it stopped and resumes on your Telegram reply. No polling, no restart. |
| **Session saved per domain** | Log in once. Later runs restore cookies and skip straight to discovery. |
| **CDP mode by default** | Playwright cannot ship a Chromium binary for Android. We attach to one Termux installs itself. |

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
LLM_API_KEY=...               # OpenRouter / OpenAI / Anthropic / Gemini
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
| `LLM_PROVIDER` | `openrouter` | `openai` · `anthropic` · `gemini` · `openrouter` |
| `BROWSER_MODE` | `cdp` | `cdp` attaches to a running Chromium; `launch` lets Playwright start one |
| `CDP_URL` | `http://127.0.0.1:9222` | Where the browser is listening |
| `DRY_RUN` | `false` | Plan everything, click nothing |
| `MAX_ACTIONS` | `120` | Hard ceiling on browser actions per run |
| `MAX_DISCOVERY_PAGES` | `25` | Crawl budget |
| `HUMAN_GATE_TIMEOUT` | `600` | Seconds a gate waits for you |

---

## Project layout

```
config/settings.py     typed config, loaded once
core/bus.py            async event bus (agents → Telegram/CLI)
core/state.py          RunState, persisted after every phase
core/llm.py            provider-agnostic LLM client
core/guardrails.py     domain lock · intent filter · action budget
browser/driver.py      Playwright lifecycle, CDP attach
browser/dom.py         indexed snapshot of interactive elements
browser/session.py     per-domain cookie persistence
agents/                base · browser · login · discovery · miner
                       · planner · verifier · reporter · orchestrator
tgbot/bot.py           Telegram commands
tgbot/human_gate.py    OTP/CAPTCHA suspension
```

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
