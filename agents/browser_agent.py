"""The only component allowed to touch the page.

Every other agent reasons; this one acts. Each action passes through the
guardrails first and returns a structured result the verifier can inspect.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from agents.base import Agent
from browser.dom import Snapshot, selector_for, snapshot as take_snapshot
from browser.driver import BrowserDriver
from core.bus import EventBus
from core.guardrails import Guardrails
from core.llm import LLMClient
from core.secrets import SecretVault
from core.state import RunState

VALID_ACTIONS = {"goto", "click", "fill", "press", "select", "scroll", "wait", "screenshot"}


@dataclass(slots=True)
class ActionResult:
    ok: bool
    action: str
    detail: str = ""
    blocked: bool = False
    url_before: str = ""
    url_after: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        mark = "ok" if self.ok else ("blocked" if self.blocked else "failed")
        return f"{self.action}: {mark} — {self.detail}"


class BrowserAgent(Agent):
    name = "browser"

    def __init__(
        self,
        bus: EventBus,
        state: RunState,
        driver: BrowserDriver,
        guardrails: Guardrails,
        llm: LLMClient | None = None,
        dry_run: bool = False,
        vault: SecretVault | None = None,
    ) -> None:
        super().__init__(bus, state, llm)
        self.driver = driver
        self.guardrails = guardrails
        self.dry_run = dry_run
        # The browser layer is the ONLY place a credential is turned back into
        # plaintext, and only in the instruction immediately before typing it.
        self.vault = vault
        self._snapshot: Snapshot | None = None

    @property
    def page(self):
        return self.driver.page

    async def refresh(self) -> Snapshot:
        self._snapshot = await take_snapshot(self.page)
        return self._snapshot

    async def current(self) -> Snapshot:
        return self._snapshot or await self.refresh()

    def _target_host(self) -> str:
        return urlparse(self.state.target_url).netloc.lower().removeprefix("www.")

    async def focus_latest_page(self) -> None:
        """Point the driver at the most recently opened page (OAuth popups)."""
        await self.driver.focus_latest_page()
        self._snapshot = None

    async def focus_target_page(self) -> None:
        """Prefer a page whose URL is back on the run's target origin."""
        await self.driver.focus_page_for_host(self._target_host())
        self._snapshot = None

    # ── action dispatch ──────────────────────────────────────────────────────
    async def execute(self, action: dict[str, Any]) -> ActionResult:
        kind = str(action.get("action", "")).lower().strip()
        if kind not in VALID_ACTIONS:
            return ActionResult(False, kind or "unknown", f"unsupported action {kind!r}")

        label = await self._label_for(action)
        verdict = self.guardrails.check_action(kind, label=label, url=str(action.get("url", "")))
        if not verdict:
            await self.warn(f"Blocked {kind}: {verdict.reason}")
            return ActionResult(False, kind, verdict.reason, blocked=True)

        url_before = self.page.url
        if self.dry_run and kind not in {"wait", "screenshot", "scroll"}:
            await self.step(f"[dry-run] would {kind} {label or action.get('url', '')}")
            return ActionResult(True, kind, "dry-run, not executed", url_before=url_before)

        extra: dict[str, Any] = {}
        try:
            detail = await self._perform(kind, action, extra)
        except PermissionError as exc:
            await self.warn(f"Refused {kind}: {exc}")
            return ActionResult(False, kind, str(exc)[:200], blocked=True, url_before=url_before)
        except Exception as exc:
            await self.warn(f"{kind} failed: {exc}")
            return ActionResult(False, kind, str(exc)[:200], url_before=url_before)

        self.guardrails.consume()
        self.state.actions_used = self.guardrails.actions_used
        await self._settle()
        url_after = self.page.url
        await self.refresh()
        await self.step(f"{kind} — {detail}")
        return ActionResult(
            True, kind, detail, url_before=url_before, url_after=url_after, data=extra
        )

    async def _perform(self, kind: str, action: dict[str, Any], extra: dict[str, Any]) -> str:
        if kind == "goto":
            url = str(action["url"])
            await self.page.goto(url, wait_until="domcontentloaded")
            return url

        if kind == "wait":
            ms = int(action.get("ms", 1500))
            await asyncio.sleep(min(ms, 15_000) / 1000)
            return f"{ms}ms"

        if kind == "scroll":
            amount = int(action.get("amount", 800))
            await self.page.mouse.wheel(0, amount)
            return f"{amount}px"

        if kind == "screenshot":
            path = await self.driver.screenshot(str(action.get("name", "shot")))
            return str(path) if path else "screenshot unavailable"

        if kind == "press":
            key = str(action.get("key", "Enter"))
            await self.page.keyboard.press(key)
            return key

        selector = selector_for(int(action["index"]))
        locator = self.page.locator(selector).first
        await locator.scroll_into_view_if_needed(timeout=5000)

        if kind == "click":
            expect_popup = bool(action.get("expect_popup"))
            if expect_popup and self.driver.context is not None:
                try:
                    async with self.driver.context.expect_page(timeout=8_000) as page_info:
                        await locator.click(timeout=10_000)
                    popup = await page_info.value
                    try:
                        await popup.wait_for_load_state("domcontentloaded", timeout=10_000)
                    except Exception:
                        pass
                    self.driver.set_page(popup)
                    self._snapshot = None
                    extra["popup_url"] = popup.url
                    return f"element #{action['index']} (popup {popup.url[:80]})"
                except Exception:
                    # Popup may be same-tab redirect, or blocked — fall through.
                    pass
            await locator.click(timeout=10_000)
            return f"element #{action['index']}"

        if kind == "fill":
            raw = str(action.get("text", ""))
            element = (await self.current()).find(int(action["index"]))
            is_token = bool(self.vault and self.vault.contains_token(raw))

            # A sensitive field may only be filled from the vault. If a planner
            # ever invents a password, refuse it rather than type it.
            if element is not None and element.sensitive and not is_token:
                raise PermissionError(
                    f"element #{action['index']} is a credential field; "
                    "it can only be filled with a vault token"
                )

            value = self.vault.resolve(raw) if self.vault else raw
            await locator.fill(value)
            shown = "<secret>" if is_token else raw[:40]
            return f"element #{action['index']} = {shown!r}"

        if kind == "select":
            value = str(action.get("text", ""))
            await locator.select_option(value)
            return f"element #{action['index']} -> {value!r}"

        return "no-op"

    async def _settle(self) -> None:
        """Give SPAs a moment; never let a hung network call stall the run."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            await asyncio.sleep(0.5)

    async def _label_for(self, action: dict[str, Any]) -> str:
        if "index" not in action:
            return ""
        snap = await self.current()
        element = snap.find(int(action["index"]))
        return element.label if element else ""

    async def run(self, actions: list[dict[str, Any]]) -> list[ActionResult]:
        results: list[ActionResult] = []
        for action in actions:
            result = await self.execute(action)
            results.append(result)
            if not result.ok and not result.blocked:
                break
        return results
