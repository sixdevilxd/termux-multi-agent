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

# ─── Site login (optional) ─────────────────────────────────────────────────
# Credentials for the site you automate. Leave blank and the bot will ask you
# on Telegram instead, once per session.
#
# These never reach the AI model: they go straight into the run's secret vault
# and are resolved only in the instruction before the browser types them.
#
# LOGIN_DOMAIN binds them to one host. WITHOUT IT, anyone who can send
# /run <url> can have your password typed into a site of their choosing.
LOGIN_EMAIL=
LOGIN_PASSWORD=
LOGIN_DOMAIN=

# Abort the run if authentication cannot be confirmed. Rewards only accrue on
# a logged-in account, so an anonymous run burns the action budget and the
# model quota for nothing. Set to false only if you deliberately want to
# survey a site without signing in.
REQUIRE_LOGIN=true

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
| `LLM_PROVIDER` | `agentrouter` | `agentrouter` · `openai` · `anthropic` · `gemini` · `openrouter` · `claude_cli` |
| `LLM_MODEL` | `claude-opus-5` | Exact gateway model id |
| `LLM_BASE_URL` | provider default | Host only; scheme and API path are derived |
| `CLAUDE_BIN` | `claude` | `claude_cli` only — path to the Claude Code binary |
| `CLAUDE_TIMEOUT` | `180` | `claude_cli` only — seconds per generation |
| `LOGIN_EMAIL` | *(empty)* | Stored site login; blank means ask on Telegram |
| `LOGIN_PASSWORD` | *(empty)* | Stored site password |
| `LOGIN_DOMAIN` | *(empty)* | Host the credentials may be used on — **set this** |
| `REQUIRE_LOGIN` | `true` | Abort the run if authentication is not confirmed |
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
| `claude_cli` | *(none — local process)* | `claude --print` | Claude Code CLI |

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
> serve arbitrary API clients. Or use `claude_cli` below, which routes through
> Claude Code itself.

### `claude_cli` — generate through the Claude Code CLI

Instead of speaking HTTP, this provider shells out to Claude Code in headless
mode (`claude --print --output-format json`).

The point is **client identity**. Gateways that whitelist applications accept
Claude Code, so if that is where your credits live, this is how the agents
reach them. The request is genuinely issued by Claude Code, with whatever
`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` that CLI is already configured
with — this project never sees the credential.

```ini
LLM_PROVIDER=claude_cli
LLM_MODEL=                 # blank = whatever the CLI defaults to
LLM_API_KEY=               # not used
LLM_BASE_URL=              # not used
# CLAUDE_BIN=claude        # override if it is not on PATH
# CLAUDE_TIMEOUT=180       # seconds per generation
```

Set the CLI up first, then confirm it answers before pointing the bot at it:

```bash
export ANTHROPIC_AUTH_TOKEN="..."
export ANTHROPIC_BASE_URL="https://agentrouter.org"
claude -p "say pong"
```

Trade-offs worth knowing:

- **Slower.** Every generation spawns a process. Expect several seconds of
  overhead per call, and a run makes tens of calls.
- **No temperature control.** The flag is ignored.
- **No model list.** `run.py --models` cannot work; run `claude` interactively
  and use `/model`.
- **Tools are disabled** on purpose — this path is text generation, not an
  agent that acts. Claude Code is invoked with `--max-turns 1` and
  `--disallowedTools Bash,Read,Write,Edit,...`.

Model ids differ between gateways — `claude-opus-5` here, but
`anthropic/claude-opus-5` on OpenRouter. `python run.py --models` prints the
authoritative list and flags your `LLM_MODEL` if it is not on it.

---

## Stored site credentials

Set `LOGIN_EMAIL` and `LOGIN_PASSWORD` and the agent logs in without asking.
Leave them blank and it asks on Telegram instead, once per session — the saved
browser session then keeps you signed in for later runs.

```ini
LOGIN_EMAIL=you@example.com
LOGIN_PASSWORD=your-password
LOGIN_DOMAIN=xclass.xiiid.ai
```

**Always set `LOGIN_DOMAIN`.** The bot runs whatever URL it is given, so
unbound credentials can be typed into any site someone sends to `/run`. With
the binding in place, a run against another host silently falls back to asking
on Telegram. `LOGIN_DOMAIN` accepts any spelling — `example.com`,
`www.example.com`, `https://app.example.com/` — and subdomains of the bound
host are included.

`run.py --check` reports the binding without ever printing the values:

```
login creds: stored, bound to xclass.xiiid.ai
```

The credentials are vaulted the moment they are read: the planner, the run
state, the logs and the report only ever see `{{secret:s1}}`, and every prompt
is scrubbed before it leaves for the model. See
[ARCHITECTURE.md](ARCHITECTURE.md#credential-isolation).

Your `.env` still holds a plaintext password, so restrict it:

```bash
chmod 600 .env
```

### Anonymous runs are refused by default

`REQUIRE_LOGIN=true` (the default) stops the run the moment authentication
cannot be confirmed, before discovery spends any budget:

```
x orchestrator Not authenticated — stopping. Rewards only accrue on a
               logged-in account, so an anonymous run would be wasted.
```

The report then names the cause: no credentials available, credentials that
did not work, or a login flow that could not be detected at all. Set
`REQUIRE_LOGIN=false` only when you deliberately want to survey a site while
signed out.

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
