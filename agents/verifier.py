"""Did the task actually complete, or did the model just believe it did?

Two layers: cheap deterministic signals first, LLM adjudication only when the
signals are ambiguous. This is what turns the pipeline from a one-way street
into a loop — a failed verdict sends the task back to the planner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agents.base import Agent
from browser.dom import Snapshot
from core.state import Task

SUCCESS_HINTS = re.compile(
    r"\b(completed|complete|success|claimed|done|selesai|berhasil|congratulations|"
    r"well done|you earned|reward claimed|already claimed|come back tomorrow)\b",
    re.I,
)
FAILURE_HINTS = re.compile(
    r"\b(error|failed|gagal|try again|something went wrong|not eligible|expired)\b", re.I
)
POINTS = re.compile(r"([+-]?\d[\d,\.]*)\s*(xp|points?|poin|coins?|credits?|gems?)\b", re.I)

SYSTEM = """You verify whether a web task was completed.

You get: the goal, the actions taken, and the final page state. Decide if the
goal was genuinely achieved. Be strict — "the button was clicked" is not the
same as "the task completed". Absence of evidence means not verified.

Return JSON:
{"verified": bool, "confidence": 0.0-1.0, "evidence": "quote from the page",
 "retry_hint": "what to try differently, or empty string"}"""


@dataclass(slots=True)
class Verdict:
    verified: bool
    confidence: float
    evidence: str = ""
    retry_hint: str = ""
    source: str = "heuristic"


class Verifier(Agent):
    name = "verifier"

    async def run(
        self,
        task: Task,
        before: Snapshot,
        after: Snapshot,
        history: list[str],
    ) -> Verdict:
        verdict = self._heuristics(before, after)
        if verdict is not None:
            await self.info(f"Verified by signal: {verdict.evidence[:80]}")
            task.evidence = {"source": verdict.source, "evidence": verdict.evidence}
            return verdict

        if self.llm is None:
            return Verdict(False, 0.0, "no signal and no LLM available", "retry once")

        prompt = (
            f"GOAL: {task.title}\n"
            f"ACTIONS TAKEN:\n" + "\n".join(f"- {h}" for h in history[-10:]) + "\n\n"
            f"FINAL PAGE:\n{after.render(max_elements=40)}"
        )
        try:
            raw: Any = await self.llm.json(SYSTEM, prompt)
        except Exception as exc:
            await self.warn(f"Verification LLM call failed: {exc}")
            return Verdict(False, 0.0, str(exc)[:120], "retry")

        verdict = Verdict(
            verified=bool(raw.get("verified")),
            confidence=float(raw.get("confidence", 0.0) or 0.0),
            evidence=str(raw.get("evidence", ""))[:300],
            retry_hint=str(raw.get("retry_hint", ""))[:200],
            source="llm",
        )
        task.evidence = {"source": "llm", "evidence": verdict.evidence}
        await self.info(f"LLM verdict: verified={verdict.verified} ({verdict.confidence:.2f})")
        return verdict

    # ── deterministic signals ────────────────────────────────────────────────
    def _heuristics(self, before: Snapshot, after: Snapshot) -> Verdict | None:
        tail = after.digest[-1500:]

        if FAILURE_HINTS.search(tail) and not SUCCESS_HINTS.search(tail):
            return Verdict(False, 0.8, _first_match(FAILURE_HINTS, tail), "page reported an error")

        delta = _points_delta(before.digest, after.digest)
        if delta is not None and delta > 0:
            return Verdict(True, 0.9, f"score increased by {delta}", source="points-delta")

        if SUCCESS_HINTS.search(tail):
            return Verdict(True, 0.75, _first_match(SUCCESS_HINTS, tail), source="success-text")

        return None


def _first_match(pattern: re.Pattern[str], text: str, window: int = 90) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    start = max(0, match.start() - window // 2)
    return text[start : start + window].strip()


def _points_delta(before: str, after: str) -> float | None:
    def total(text: str) -> float | None:
        values = [
            float(m.group(1).replace(",", ""))
            for m in POINTS.finditer(text)
            if m.group(1).replace(",", "").replace(".", "").isdigit()
        ]
        return sum(values) if values else None

    a, b = total(before), total(after)
    if a is None or b is None:
        return None
    return b - a
