"""Shared agent scaffolding."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.bus import Event, EventBus
from core.llm import LLMClient
from core.logger import get_logger
from core.state import RunState


class Agent(ABC):
    name: str = "agent"

    def __init__(self, bus: EventBus, state: RunState, llm: LLMClient | None = None) -> None:
        self.bus = bus
        self.state = state
        self.llm = llm
        self.log = get_logger(self.name)

    async def emit(self, kind: str, message: str, **data: Any) -> None:
        self.log.info(message) if kind != "error" else self.log.error(message)
        await self.bus.publish(
            Event(run_id=self.state.run_id, agent=self.name, kind=kind, message=message, data=data)
        )

    async def info(self, message: str, **data: Any) -> None:
        await self.emit("info", message, **data)

    async def step(self, message: str, **data: Any) -> None:
        await self.emit("step", message, **data)

    async def warn(self, message: str, **data: Any) -> None:
        await self.emit("warn", message, **data)

    async def fail(self, message: str, **data: Any) -> None:
        await self.emit("error", message, **data)

    def require_llm(self) -> LLMClient:
        if self.llm is None:
            raise RuntimeError(f"{self.name} requires an LLM client but none was provided.")
        return self.llm

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        ...
