"""A tiny async event bus.

Every agent publishes progress here. The Telegram bot and the CLI both just
subscribe — that is the only coupling between the pipeline and its front-ends.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

Handler = Callable[["Event"], Awaitable[None]]


@dataclass(slots=True)
class Event:
    run_id: str
    agent: str
    kind: str  # info | step | warn | error | gate | done
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def pretty(self) -> str:
        icon = {
            "info": "•",
            "step": "→",
            "warn": "!",
            "error": "x",
            "gate": "?",
            "done": "v",
        }.get(self.kind, "•")
        return f"{icon} [{self.agent}] {self.message}"


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._history: list[Event] = []
        self._lock = asyncio.Lock()

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        self._handlers.append(handler)

        def _unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return _unsubscribe

    async def publish(self, event: Event) -> None:
        async with self._lock:
            self._history.append(event)
        for handler in list(self._handlers):
            try:
                await handler(event)
            except Exception:  # a broken subscriber must never kill the pipeline
                pass

    def history(self, run_id: str | None = None) -> list[Event]:
        if run_id is None:
            return list(self._history)
        return [e for e in self._history if e.run_id == run_id]
