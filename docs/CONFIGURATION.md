# Configuration reference

Every setting lives in `.env`. Only three values are required; everything else
has a working default.

Booleans accept `1`, `true`, `yes`, `on` — anything else is false.

---

## Complete `.env`

```ini
# ─── Telegram ──────────────────────────────────────────────────────────────
# REQUIRED. From @BotFather.
TELEGRAM_BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# REQUIRED. Comma-separated numeric Telegram user ids allowed to command the
# bot. Get yours from @userinfobot. Empty means the bot refuses every command
# and will not even start — that is deliberate, not a bug.
TELEGRAM_ALLOWED_USERS=123456789

# ─── LLM provider ──────────────────────────────────────────────────────────
# one of: agentrouter | openai | anthropic | gemini | openrouter
LLM_PROVIDER=agentrouter

# Exact model id the gateway accepts. Verify: python run.py --models claude
LLM_MODEL=claude-opus-5

# REQUIRED. From https://agentrouter.org/console
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Host only — scheme and API path are derived, so do NOT write /v1 here.
# Equivalent: agentrouter.org | https://agentrouter.org
# Mainland China mirror: ps.air-outer.com
LLM_BASE_URL=agentrouter.org

# ─── Browser ───────────────────────────────────────────────────────────────
# launch = Playwright starts the browser itself  (Debian/proot, desktop Linux)
# cdp    = attach to a Chromium you started      (native Termux)
BROWSER_MODE=launch

# Required for launch mode on Android/proot: the system Chromium binary.
# Find it with: which chromium || which chromium-browser
CHROME_PATH=/usr/bin/chromium

# Extra Chromium flags, comma separated. proot often needs these to start:
# BROWSER_ARGS=--single-process,--no-zygote

# Only used in cdp mode. Must match scripts/start_chromium.sh
CDP_URL=http://127.0.0.1:9222

# Only used in launch mode; cdp inherits the running browser
HEADLESS=true

# Per-navigation timeout, milliseconds
NAV_TIMEOUT_MS=30000

# ─── Safety ────────────────────────────────────────────────────────────────
# true = reason about everything, click nothing. Use this on a new site first.
DRY_RUN=false

# Hard ceiling on browser actions per run
MAX_ACTIONS=120

# Discovery crawl budget
MAX_DISCOVERY_PAGES=25
MAX_DISCOVERY_DEPTH=3

# Seconds a human gate (OTP / CAPTCHA / password) waits for your reply
HUMAN_GATE_TIMEOUT=600

# ─── Storage ───────────────────────────────────────────────────────────────
# storage/sessions/ holds live auth cookies — gitignored, written 0600
STORAGE_DIR=./storage

# DEBUG | INFO | WARNING | ERROR
LOG_LEVEL=INFO

# ─── Chromium launcher (read by scripts/start_chromium.sh, not by Python) ───
# CDP_PORT=9222
# CDP_PROFILE=$HOME/.cache/agent-chromium
```

---

## Reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | **Required.** Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USERS` | — | **Required.** Allowed numeric user ids, comma separated |
| `LLM_API_KEY` | — | **Required.** Gateway API key |
| `LLM_PROVIDER` | `agentrouter` | `agentrouter` · `openai` · `anthropic` · `gemini` · `openrouter` |
| `LLM_MODEL` | `claude-opus-5` | Exact gateway model id |
| `LLM_BASE_URL` | provider default | Host only; scheme and API path are derived |
| `BROWSER_MODE` | `cdp` | `launch` starts a browser; `cdp` attaches to one |
| `CHROME_PATH` | *(empty)* | System Chromium binary for `launch` mode |
| `BROWSER_ARGS` | *(empty)* | Extra Chromium flags, comma separated |
| `CDP_URL` | `http://127.0.0.1:9222` | Where the browser listens in `cdp` mode |
| `HEADLESS` | `true` | Headless in `launch` mode |
| `NAV_TIMEOUT_MS` | `30000` | Per-navigation timeout |
| `DRY_RUN` | `false` | Plan everything, click nothing |
| `MAX_ACTIONS` | `120` | Hard action ceiling per run |
| `MAX_DISCOVERY_PAGES` | `25` | Crawl budget |
| `MAX_DISCOVERY_DEPTH` | `3` | Crawl depth |
| `HUMAN_GATE_TIMEOUT` | `600` | Seconds a gate waits for you |
| `STORAGE_DIR` | `./storage` | Sessions, runs, reports, screenshots |
| `LOG_LEVEL` | `INFO` | Console verbosity |

---

## Providers

| Provider | Host | Request path | Wire format |
| --- | --- | --- | --- |
| `agentrouter` | `agentrouter.org` | `/v1/chat/completions` | OpenAI |
| `openai` | `api.openai.com` | `/v1/chat/completions` | OpenAI |
| `openrouter` | `openrouter.ai/api` | `/v1/chat/completions` | OpenAI |
| `anthropic` | `api.anthropic.com` | `/v1/messages` | Anthropic |
| `gemini` | `generativelanguage.googleapis.com` | `/v1beta/models/...` | Gemini |

**You only ever configure the host.** `agentrouter.org`,
`https://agentrouter.org` and `https://agentrouter.org/v1` all resolve to the
same API root — the scheme is added if missing and `/v1` is appended only when
absent, so it can never be doubled.

AgentRouter serves both wire formats from one host:

```ini
LLM_PROVIDER=agentrouter   # -> POST agentrouter.org/v1/chat/completions
LLM_PROVIDER=anthropic     # -> POST agentrouter.org/v1/messages
LLM_BASE_URL=agentrouter.org
```

Either reaches `claude-opus-5`. Start with `agentrouter`.

> **AgentRouter restricts which client applications may use a key.** Only apps
> on their whitelist — Claude Code, Codex, Cline, Roo Code, Copilot, Qwen Code
> — are accepted. A custom API client like this one is refused with
> `HTTP 401 … "type": "unauthorized_client_error"` and the message
> *"unauthorized client detected"*, **even when the key is valid**. See
> [agentrouter-org/docs#21](https://github.com/agentrouter-org/docs/issues/21).
> Use `openrouter`, `gemini`, `openai` or `anthropic` instead — all of them
> serve arbitrary API clients.

Model ids differ between gateways — `claude-opus-5` here, but
`anthropic/claude-opus-5` on OpenRouter. `python run.py --models` prints the
authoritative list and flags your `LLM_MODEL` if it is not on it.

---

## Diagnostics

```bash
python run.py --check             # config parses; paths and URLs resolve
python run.py --models [filter]   # model ids the gateway accepts
python run.py --ping              # one real completion, end to end
```

`--check` prints the derived values, which is usually enough to spot the
problem:

```
provider   : agentrouter / claude-opus-5
base url   : https://agentrouter.org        <- what you set
api root   : https://agentrouter.org/v1     <- what gets called
chromium   : /usr/bin/chromium
```

---

## Common configuration errors

| Message | Cause |
| --- | --- |
| `TELEGRAM_ALLOWED_USERS is empty — refusing to start` | Not set. Fail-closed by design. |
| `CHROME_PATH points at ... which does not exist` | Wrong path. Run `which chromium`. |
| `BROWSER_MODE=launch without CHROME_PATH` | On Android/proot you must supply a system browser. |
| `LLM_MODEL ... is not in this list` | Gateway-specific slug. Copy one from `--models`. |
| `HTTP 401` / `403` | Bad or expired `LLM_API_KEY`. |
| `unauthorized client detected` / `unauthorized_client_error` | The gateway rejected the **client**, not the key. AgentRouter only accepts whitelisted apps. Switch provider. |
| `LLM_BASE_URL points at X, but LLM_PROVIDER=Y normally uses Z` | You switched providers without clearing `LLM_BASE_URL`. |
| `bot-protection challenge instead of the API` | WAF blocked a datacenter/VPN IP. Disable VPN or use the mirror. |
| `The gateway returned a web page, not the API` | `LLM_BASE_URL` points at the website, not the API host. |
