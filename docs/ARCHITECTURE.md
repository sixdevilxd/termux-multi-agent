# Architecture

## Agent roster

| Agent | Input | Output | Uses LLM |
| --- | --- | --- | --- |
| `Orchestrator` | target URL | `RunState` | no |
| `BrowserAgent` | action dict | `ActionResult` | no |
| `LoginDetector` | `Snapshot` | authenticated bool | no (heuristic) |
| `SiteUnderstanding` | `Snapshot` | site model + vocabulary | **yes** |
| `DiscoveryAgent` | start URL + site model | `list[PageInfo]` | occasionally (bounded) |
| `TaskMiner` | `list[PageInfo]` | ranked `list[Task]` | **yes** |
| `TaskPlanner` | `Task` + `Snapshot` | 1-4 actions | **yes** |
| `Verifier` | before/after `Snapshot` | `Verdict` | only when signals are silent |
| `Reporter` | `RunState` | markdown | no |

Everything mechanical — crawling, clicking, cookie handling, login shape
detection, counter arithmetic — is deterministic code. Only judgement calls
spend tokens, which keeps a full run in the low tens of thousands of tokens.

## Pipeline

```
open → login → understand → discovery → mine → [plan → act → verify]* → report
```

The bracketed part is a loop. A failed verification feeds its retry hint back
into the planner until the attempt budget runs out.

## Why "understand" comes before "discover"

The naive version of this system is:

```
login → find /tasks → done
```

which fails on every site that does not have a `/tasks` page. The slightly
less naive version scores links against a hardcoded keyword list —
`quest`, `reward`, `xp` — which fails on every site that calls its currency
*Sparks*, or ships its UI in Indonesian, or hides its daily action behind a
button labelled *Ambil*.

So the first thing the agent does after login is read the landing page and
answer four questions:

1. What is this product for?
2. What words does **this site** use for its rewards?
3. Which reward systems exist, and where is progress displayed?
4. Which areas are worth exploring — and which must be left alone?

`DiscoveryAgent` then scores its frontier using the learned vocabulary at
weight 4 and a weak generic fallback at weight 1, seeds itself from the
shortlist the understanding agent produced, and lets the model re-rank the
frontier at most twice more as the picture fills in.

`TaskMiner` receives the same vocabulary and is told explicitly that an
activity need not be labelled a task:

> a lesson with a Continue button · a daily claim/collect/spin · an unwatched
> video · a survey that grants progress · a streak that continues by acting
> today · a tier that fills as you go

## Data flow

```
RunState
 ├── understanding: dict          written by SiteUnderstanding
 │     ├── site_purpose, site_type
 │     ├── reward_vocabulary      drives discovery scoring + counter parsing
 │     ├── reward_systems
 │     └── explore / avoid
 ├── reward_baseline / _final     counters before and after the run
 ├── pages: list[PageInfo]        written by DiscoveryAgent
 ├── tasks: list[Task]            written by TaskMiner, mutated by the exec loop
 ├── actions_used: int            incremented by BrowserAgent via Guardrails
 └── notes / error                terminal diagnostics
```

`RunState` is saved to `storage/runs/<run_id>.json` after every phase, so a
crashed run leaves a forensic trail rather than nothing.

## Credential isolation

**A secret never enters an LLM prompt, a log line, or the run state.**

```
HumanGate.ask("Password?")
        │  plaintext exists for exactly one statement
        ▼
SecretVault.put("password", ...) ──► "{{secret:s1}}"
        │
        ├─► planner / RunState / logs / report   see only the token
        │
        └─► BrowserAgent._perform("fill")
                 vault.resolve(token) ──► locator.fill(plaintext)
```

Five independent defences:

1. `browser/dom.py` never reads the value of a sensitive input. Detection is
   by `type="password"` plus a regex over `name`/`id`/`autocomplete`/
   `placeholder` (`pass|otp|code|token|secret|cvv|pin|seed|mnemonic|private`).
2. Credentials are vaulted the moment the human gate returns them.
3. `LLMClient` runs `vault.redact()` over every system and user prompt before
   egress — belt and braces if a secret ever lands in a page digest.
4. A field flagged `sensitive` can **only** be filled from a vault token. A
   plaintext fill is refused with `PermissionError` and surfaces as a blocked
   action, so an LLM that invents a password cannot get it typed.
5. `vault.clear()` runs in the orchestrator's `finally` block.

`tests/test_credential_safety.py` drives a fake page and asserts all of this:
the browser receives the real value, while `RunState` and the action result
contain only `<secret>`.

## Verification

Cheapest, most objective signal first:

1. **Counter delta** — reward units parsed with the site's own vocabulary. If
   `sparks` went 1,240 → 1,290, the task worked. Confidence 0.92.
2. **Failure text** — an explicit error on the page. Confidence 0.8.
3. **Success text** — "completed", "claimed", "selesai", "already claimed".
   Confidence 0.75.
4. **LLM adjudication** — only when 1-3 are all silent.

`core/rewards.py` builds its pattern at runtime from
`GENERIC_UNITS ∪ reward_vocabulary`, handles both `120 XP` and `XP: 120`,
tolerates a duration word in between (`7 day Streak`, `30-day streak`), and
keeps the **highest** value per unit so a running total wins over a `+10`
increment.

## Page representation

`browser/dom.py` runs two JS passes over shared helpers:

1. tag every visible interactive element with `data-agent-idx`
2. read them back as `{index, tag, type, role, label, href, sensitive}`

Actions target `[data-agent-idx="N"]`. Indices are stable *within one snapshot
only* — the browser agent re-snapshots after every action, and the planner
validates that any index it emits still exists.

The model sees roughly 500-1500 tokens per page instead of 50k+ of raw HTML:

```
URL: https://app.example.com/learn
TITLE: Keep learning
INTERACTIVE ELEMENTS:
[0] <link> 'Dashboard' -> /dashboard
[1] <button> 'Continue lesson 4'
[2] <password> 'Password' (secret field — fill only via a vault token)
PAGE TEXT: 1,240 Sparks · 7 day Streak · Finish today's lesson to ...
```

## Guardrails

Applied in `BrowserAgent.execute` before anything touches the page:

1. **Budget** — `actions_used < MAX_ACTIONS`.
2. **Domain lock** — `goto` targets must stay on the target origin or a subdomain.
3. **Intent filter** — regex over the element's accessible label across three
   categories: destructive, financial, social. English and Indonesian terms.
4. **Credential rule** — plaintext into a sensitive field is refused.

A block is not an exception. It returns `ActionResult(blocked=True)`, is
appended to the planner's history as *"guardrail refused that control; choose
another path"*, and the loop continues. The report lists every block.

## Human gate

```
agent coroutine          HumanGate              Telegram
      │                      │                      │
      │── ask("OTP?") ──────►│                      │
      │                      │── notifier(text) ───►│
      │   (suspended on a    │                      │
      │    Future)           │◄── /reply ab12 4711 ─│
      │◄──── "4711" ─────────│                      │
```

`asyncio.wait_for` bounds the wait at `HUMAN_GATE_TIMEOUT`. On timeout the
future resolves to `None` and the calling agent degrades gracefully instead of
hanging forever.

## Extending

**New task type** — add it to `VALID_TYPES` in `agents/task_miner.py` and to
the system prompt. The planner is type-agnostic.

**New LLM provider** — add a branch in `LLMClient.chat` and an entry in
`DEFAULT_BASE_URLS`. OpenAI-compatible endpoints need neither.

**New front-end** — subscribe to the `EventBus` and implement a `HumanGate`
notifier. The pipeline has no idea Telegram exists.

**Stricter policy** — extend the pattern lists in `core/guardrails.py`, or pass
`block_social=False` if the target legitimately requires posting.
