"""The conductor: wires every agent together and owns the retry loop.

    login -> discovery -> mine -> [plan -> act -> verify]* -> report

The bracketed part is a loop, not a straight line. A failed verification feeds
its retry hint back into the planner until the attempt budget runs out.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agents.browser_agent import BrowserAgent
from agents.discovery import DiscoveryAgent
from agents.login_detector import LoginDetector
from agents.planner import TaskPlanner
from agents.reporter import Reporter
from agents.site_understanding import SiteUnderstanding
from agents.task_miner import TaskMiner
from agents.verifier import Verifier
from browser.driver import BrowserDriver
from config.settings import settings
from core.bus import Event, EventBus
from core.guardrails import Guardrails
from core.llm import LLMClient
from core.logger import get_logger
from core.rewards import describe_delta, extract_counters
from core.secrets import SecretVault
from core.state import RunState, Task

log = get_logger("orchestrator")

MAX_ATTEMPTS_PER_TASK = 2
MAX_PLAN_ROUNDS = 8


class Orchestrator:
    def __init__(
        self,
        target_url: str,
        bus: EventBus,
        human_gate,
        credentials: dict[str, str] | None = None,
        max_tasks: int = 10,
    ) -> None:
        self.state = RunState(target_url=target_url)
        self.bus = bus
        self.gate = human_gate
        self.credentials = credentials or {}
        self.max_tasks = max_tasks
        self.guardrails = Guardrails(target_url)
        # One vault per run. The LLM client is bound to it so every prompt is
        # scrubbed on the way out.
        self.vault = SecretVault()
        self.llm = LLMClient(vault=self.vault)
        self._cancelled = False
        self._browser: BrowserAgent | None = None

    def cancel(self) -> None:
        self._cancelled = True
        self.gate.cancel_all()

    async def _announce(self, kind: str, message: str, **data: Any) -> None:
        await self.bus.publish(
            Event(run_id=self.state.run_id, agent="orchestrator", kind=kind,
                  message=message, data=data)
        )

    # ── main pipeline ────────────────────────────────────────────────────────
    async def run(self) -> RunState:
        settings.ensure_dirs()
        state = self.state
        state.save()
        await self._announce("info", f"Run {state.run_id} starting on {state.target_url}")
        if settings.dry_run:
            await self._announce("warn", "DRY_RUN is on — nothing will actually be clicked.")

        driver = BrowserDriver(state.target_url)
        try:
            await driver.start()
            browser = BrowserAgent(
                self.bus, state, driver, self.guardrails, self.llm,
                dry_run=settings.dry_run, vault=self.vault,
            )
            self._browser = browser

            # 1. open the target
            state.phase = "open"
            result = await browser.execute({"action": "goto", "url": state.target_url})
            if not result.ok:
                raise RuntimeError(f"Could not open {state.target_url}: {result.detail}")

            # 2. authenticate
            state.phase = "login"
            login = LoginDetector(self.bus, state, browser, self.gate, self.llm, self.vault)
            await login.run(self.credentials)
            state.save()
            if self._cancelled:
                return await self._finish(state, "cancelled during login")

            # 3. understand what this site is before exploring it
            state.phase = "understanding"
            await SiteUnderstanding(self.bus, state, self.llm).run(await browser.refresh())
            if self._cancelled:
                return await self._finish(state, "cancelled during understanding")

            # 4. explore, steered by what we just learned
            state.phase = "discovery"
            pages = await DiscoveryAgent(
                self.bus, state, browser, self.guardrails, self.llm
            ).run(state.target_url)
            if self._cancelled:
                return await self._finish(state, "cancelled during discovery")

            # 5. mine, classify and prioritise
            state.phase = "mining"
            tasks = await TaskMiner(self.bus, state, self.llm).run(pages)
            if not tasks:
                return await self._finish(state, "no actionable tasks found")

            # 6. execute
            state.phase = "executing"
            planner = TaskPlanner(self.bus, state, self.llm)
            verifier = Verifier(self.bus, state, self.llm)
            for task in tasks[: self.max_tasks]:
                if self._cancelled:
                    break
                if self.guardrails.actions_used >= settings.max_actions:
                    await self._announce("warn", "Action budget exhausted — stopping early.")
                    task.status = "skipped"
                    continue
                await self._run_task(task, browser, planner, verifier)
                state.save()

            return await self._finish(state, "")
        except Exception as exc:  # noqa: BLE001 — the run must always produce a report
            log.exception("Run failed")
            state.error = f"{type(exc).__name__}: {exc}"
            return await self._finish(state, state.error)
        finally:
            try:
                await driver.stop()
            except Exception:
                pass
            await self.llm.aclose()
            # plaintext credentials never outlive the run
            self.vault.clear()

    # ── single task loop ─────────────────────────────────────────────────────
    async def _run_task(
        self,
        task: Task,
        browser: BrowserAgent,
        planner: TaskPlanner,
        verifier: Verifier,
    ) -> None:
        await self._announce("step", f"Task: {task.title}", task_id=task.id)

        for attempt in range(1, MAX_ATTEMPTS_PER_TASK + 1):
            task.attempts = attempt
            task.status = "running"

            nav = await browser.execute({"action": "goto", "url": task.url})
            if not nav.ok:
                task.status = "failed"
                task.error = f"cannot open task url: {nav.detail}"
                return

            before = await browser.refresh()
            history: list[str] = []
            if attempt > 1 and task.error:
                history.append(f"previous attempt failed: {task.error}")

            for _round in range(MAX_PLAN_ROUNDS):
                if self._cancelled or self.guardrails.actions_used >= settings.max_actions:
                    break
                snap = await browser.current()
                plan = await planner.run(task, snap, history)

                if plan["blocked"]:
                    history.append(f"planner blocked: {plan['blocked']}")
                    break
                if plan["done"] or not plan["actions"]:
                    break

                for action in plan["actions"]:
                    result = await browser.execute(action)
                    history.append(result.summary())
                    task.steps.append({"action": action, "result": result.summary()})
                    if result.blocked:
                        history.append("guardrail refused that control; choose another path")

            after = await browser.refresh()
            verdict = await verifier.run(task, before, after, history)

            if verdict.verified:
                task.status = "verified"
                task.error = ""
                await self._announce("done", f"Verified: {task.title}", task_id=task.id)
                return

            task.error = verdict.retry_hint or "verification failed"
            if attempt < MAX_ATTEMPTS_PER_TASK:
                await self._announce("warn", f"Retrying '{task.title}' — {task.error}")
                await asyncio.sleep(1)

        task.status = "failed"
        await self._announce("warn", f"Gave up on: {task.title}")

    # ── teardown ─────────────────────────────────────────────────────────────
    async def _finish(self, state: RunState, note: str) -> RunState:
        if note:
            state.notes.append(note)

        # Read the reward counters one last time so the report can show what
        # the run actually earned, in the site's own units.
        if self._browser is not None:
            try:
                snap = await self._browser.current()
                state.reward_final = extract_counters(snap.digest, state.vocabulary)
                delta = state.reward_delta()
                if delta:
                    state.notes.append(f"reward change: {describe_delta(delta)}")
                    await self._announce("done", f"Rewards moved: {describe_delta(delta)}")
            except Exception:
                pass

        if self.guardrails.blocked:
            state.notes.append(
                f"guardrails blocked {len(self.guardrails.blocked)} action(s): "
                + "; ".join(self.guardrails.blocked[:5])
            )
        state.phase = "reporting"
        await Reporter(self.bus, state, self.llm).run()
        state.phase = "finished"
        state.save()
        return state
