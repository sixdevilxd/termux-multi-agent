# termux-multi-agent

A multi-agent web automation system that runs **entirely from your phone** —
Termux for compute, Telegram for control, GitHub for the code.

You send a URL. A swarm of specialised agents opens the site, logs in (asking
you for the OTP when it hits one), works out what the product actually is,
explores it, identifies the activities that grant rewards, does them, verifies
the result, and sends you a report.

```
USER ──url──> ORCHESTRATOR
                   │
                   ▼
            BROWSER AGENT  (Chromium)
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

The agent works to one standing goal:

> *Understand this website and identify legitimate activities that can generate
> points, XP, badges, rewards, or campaign progress — even when those
> activities are not explicitly labelled as tasks, quests, or missions.*

---

## Documentation

| Guide | For |
| --- | --- |
| **[Setup — Debian in Termux](docs/SETUP-DEBIAN.md)** | Recommended. Chromium from apt, pip wheels that build. |
| **[Setup — native Termux](docs/SETUP-TERMUX.md)** | No Debian guest. Lighter, but two processes. |
| **[Configuration](docs/CONFIGURATION.md)** | Every `.env` variable, providers, diagnostics. |
| **[Architecture](docs/ARCHITECTURE.md)** | How the agents fit together, and why. |

Shortest path, if you already run `proot-distro` Debian:

```bash
proot-distro login debian
git clone https://github.com/sixdevilxd/termux-multi-agent
cd termux-multi-agent && bash scripts/setup_debian.sh
. .venv/bin/activate && nano .env && python run.py --bot
```

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

---

## Login is adaptive

The agent does not assume every site behaves the same way.

| Case | What happens |
| --- | --- |
| **Already authenticated** | A saved session is restored; login is skipped entirely. |
| **Email + password** | Fields located by type and metadata, filled from the vault, submitted, verified. |
| **One-click sign-in** | "Continue with Google", "Sign in with X", wallet buttons — detected and followed. |
| **OTP** | Run pauses. Telegram asks you for the code. You reply. Run resumes. |
| **CAPTCHA** | Run pauses. You solve it. The agent detects success and continues. |

Credentials are isolated by construction, not by convention: the vault issues
opaque tokens, the DOM snapshot never reads a secret field's value, a sensitive
field can only be filled from a token, and every prompt is redacted before
egress. Full detail and the threat model are in
[ARCHITECTURE.md](docs/ARCHITECTURE.md#credential-isolation);
`tests/test_credential_safety.py` proves it against a fake page.

---

## Telegram commands

| Command | Effect |
| --- | --- |
| `/run <url>` | Start a run |
| `/status` | Current phase, page/task counts, open gate |
| `/reply <token> <answer>` | Answer a human gate (OTP, password, ...) |
| `/skip <token>` | Refuse a gate, let the run continue |
| `/stop` | Cancel the current run |
| `/report` | Resend the last summary |

A plain text message answers the oldest open gate — faster than typing tokens
on a phone. Messages containing secrets are deleted after they are consumed.

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
browser/driver.py      Playwright lifecycle, CDP attach or system Chromium
browser/dom.py         indexed snapshot; never reads secret field values
browser/session.py     per-domain cookie persistence
agents/                base · browser · login · site_understanding
                       · discovery · task_miner · planner · verifier
                       · reporter · orchestrator
tgbot/bot.py           Telegram commands
tgbot/human_gate.py    OTP/CAPTCHA suspension
scripts/               setup_debian.sh · start_chromium.sh
tests/                 offline suite — no browser, network or API key needed
```

Run the tests with `python -m pytest tests/ -q`. They need no browser, no
network and no API key.

---

## Safety

Guardrails refuse any control whose label matches a destructive, financial or
public-posting pattern (`delete`, `withdraw`, `checkout`, `post`, `hapus`,
`bayar`, ...), and any navigation that leaves the target origin. Blocked
attempts are recorded in the run report.

Start with `DRY_RUN=true` on a new site. Read the report. Then turn it off.

> **Use this only on sites you own or are permitted to automate.** Automating
> quests, XP, or reward systems usually violates a site's terms of service.
> That is your call to make, and your responsibility.

Sessions in `storage/sessions/` contain live auth cookies. They are gitignored
and written `0600`. Do not commit them.

---

## Roadmap

- [ ] Resume an interrupted run from its saved `RunState`
- [ ] Vision fallback when the DOM snapshot is not enough
- [ ] Per-site plugins for known task patterns
- [ ] Scheduled runs (daily check-ins) via cron + `termux-job-scheduler`

---

## License

MIT — see [LICENSE](LICENSE).
