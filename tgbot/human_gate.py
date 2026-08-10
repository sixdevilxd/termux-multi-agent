"""The human-in-the-loop gate.

When the pipeline hits something it must not solve alone — OTP, CAPTCHA,
credentials, a risky confirmation — it calls `ask()`. That suspends the agent
coroutine on a Future and pushes the question to Telegram. Your reply resolves
the Future and the pipeline resumes exactly where it stopped.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config.settings import settings
from core.logger import get_logger

log = get_logger("gate")

Notifier = Callable[[str], Awaitable[None]]


@dataclass
class PendingQuestion:
    token: str
    prompt: str
    secret: bool
    future: asyncio.Future
    asked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HumanGate:
    """Shared between the pipeline and whichever front-end is attached."""

    def __init__(self, notifier: Notifier | None = None, timeout: int | None = None) -> None:
        self.notifier = notifier
        self.timeout = timeout or settings.human_gate_timeout
        self.pending: dict[str, PendingQuestion] = {}

    def set_notifier(self, notifier: Notifier) -> None:
        self.notifier = notifier

    @property
    def open_question(self) -> PendingQuestion | None:
        return next(iter(self.pending.values()), None)

    async def ask(self, prompt: str, secret: bool = False) -> str | None:
        """Block until the human answers, or return None on timeout."""
        token = uuid.uuid4().hex[:6]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        question = PendingQuestion(token, prompt, secret, future)
        self.pending[token] = question

        note = (
            f"*Human input needed* `#{token}`\n\n{prompt}\n\n"
            f"_Reply with:_ `/reply {token} <your answer>`\n"
            f"_Or cancel:_ `/skip {token}`\n"
            f"_Timeout: {self.timeout}s_"
        )
        log.warning("HUMAN GATE #%s: %s", token, prompt)
        if self.notifier:
            try:
                await self.notifier(note)
            except Exception as exc:
                log.error("Could not deliver gate prompt: %s", exc)
        else:
            log.warning("No notifier attached — answer via console is not supported.")

        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            log.error("Human gate #%s timed out.", token)
            if self.notifier:
                await self.notifier(f"Gate `#{token}` timed out after {self.timeout}s.")
            return None
        finally:
            self.pending.pop(token, None)

    def answer(self, token: str, value: str) -> bool:
        question = self.pending.get(token)
        if not question or question.future.done():
            return False
        question.future.set_result(value)
        return True

    def answer_latest(self, value: str) -> bool:
        question = self.open_question
        return self.answer(question.token, value) if question else False

    def skip(self, token: str) -> bool:
        question = self.pending.get(token)
        if not question or question.future.done():
            return False
        question.future.set_result(None)
        return True

    def cancel_all(self) -> None:
        for question in list(self.pending.values()):
            if not question.future.done():
                question.future.set_result(None)
        self.pending.clear()
