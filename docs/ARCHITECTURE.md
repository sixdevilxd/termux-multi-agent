# Architecture

## Agent roster

| Agent | Input | Output | Uses LLM |
| --- | --- | --- | --- |
| `Orchestrator` | target URL | `RunState` | no |
| `BrowserAgent` | action dict | `ActionResult` | no |
| `LoginDetector` | `Snapshot` | authenticated bool | no (heuristic) |
| `DiscoveryAgent` | start URL | `list[PageInfo]` | no |
| `TaskMiner` | `list[PageInfo]` | `list[Task]` | **yes** |
| `TaskPlanner` | `Task` + `Snapshot` | 1-4 actions | **yes** |
| `Verifier` | before/after `Snapshot` | `Verdict` | only when heuristics are silent |
| `Reporter` | `RunState` | markdown | no |

Only three agents spend tokens. Everything mechanical — crawling, clicking,
cookie handling, login shape detection — is deterministic code. That keeps a
full run in the low tens of thousands of tokens instead of millions.

## Data flow

```
RunState
 ├── pages: list[PageInfo]      written by DiscoveryAgent
 ├── tasks: list[Task]          written by TaskMiner, mutated by the exec loop
 ├── actions_used: int          incremented by BrowserAgent via Guardrails
 └── notes / error              terminal diagnostics
```

`RunState` is saved to `storage/runs/<run_id>.json` after every phase, so a
crashed run leaves a forensic trail rather than nothing.

## The execution loop

```python
for task in tasks:
    for attempt in 1..2:
        goto(task.url)
        before = snapshot()
        for round in 1..8:
            plan = planner(task, snapshot(), history)
            if plan.done or plan.blocked: break
            for action in plan.actions:
                history.append(browser.execute(action))
        verdict = verifier(task, before, snapshot(), history)
        if verdict.verified: break
        task.error = verdict.retry_hint      # feeds the next attempt
```

Three nested bounds — attempts, rounds, and the global action budget — mean
the loop always terminates.

## Page representation

`browser/dom.py` runs two JS passes:

1. `_TAG_JS` stamps `data-agent-idx` on every visible interactive element.
2. `_COLLECT_JS` reads them back as `{index, tag, type, role, label, href}`.

Actions then target `[data-agent-idx="N"]`. Indices are stable *within one
snapshot only* — the browser agent re-snapshots after every action, and the
planner validates that any index it emits still exists.

The model sees:

```
URL: https://app.example.com/quests
TITLE: Daily Quests
INTERACTIVE ELEMENTS:
[0] <link> 'Dashboard' -> /dashboard
[1] <button> 'Claim 50 XP'
[2] <button> 'Start quiz'
PAGE TEXT: Complete 3 quests today to earn ...
```

Roughly 500-1500 tokens per page instead of 50k+ of raw HTML.

## Guardrails

Applied in `BrowserAgent.execute` before anything touches the page:

1. **Budget** — `actions_used < MAX_ACTIONS`.
2. **Domain lock** — `goto` targets must stay on the target origin or a subdomain.
3. **Intent filter** — regex over the element's accessible label across three
   categories: destructive, financial, social. English and Indonesian terms.

A block is not an exception. It returns `ActionResult(blocked=True)`, gets
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

**New task type** — add it to the `type` enum in the `TaskMiner` system prompt.
No code change; the planner is type-agnostic.

**New LLM provider** — add a branch in `LLMClient.chat` and an entry in
`DEFAULT_BASE_URLS`. OpenAI-compatible endpoints need neither.

**New front-end** — subscribe to the `EventBus` and implement a
`HumanGate` notifier. The pipeline has no idea Telegram exists.

**Stricter policy** — extend the pattern lists in `core/guardrails.py`, or pass
`block_social=False` if the target legitimately requires posting.
