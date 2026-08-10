"""Convert one task into a short, executable action plan.

The planner works one small batch at a time and re-plans after seeing the
result — an SPA changes under you, so a 20-step plan written up front is
fiction.
"""
from __future__ import annotations

from typing import Any

from agents.base import Agent
from browser.dom import Snapshot
from core.state import Task

SYSTEM = """You drive a web browser one small batch of actions at a time.

You are given the current page (indexed interactive elements + text) and a
goal. Return the NEXT 1-4 actions only. After they run you will be shown the
new page state and asked again.

Available actions:
  {"action":"click","index":N,"reason":"..."}
  {"action":"fill","index":N,"text":"...","reason":"..."}
  {"action":"select","index":N,"text":"option value","reason":"..."}
  {"action":"press","key":"Enter","reason":"..."}
  {"action":"scroll","amount":800,"reason":"..."}
  {"action":"wait","ms":2000,"reason":"..."}

Hard rules:
- Only use an index that appears in the element list.
- Never click anything that deletes, pays, withdraws, upgrades, or posts
  publicly. Those are blocked and will fail the run.
- If the goal already looks complete, return {"actions": [], "done": true}.
- If you are stuck or the page is wrong, return
  {"actions": [], "done": false, "blocked": "why"}.

Return JSON: {"actions": [...], "done": bool, "blocked": "optional string"}"""


class TaskPlanner(Agent):
    name = "planner"

    async def run(self, task: Task, snap: Snapshot, history: list[str]) -> dict[str, Any]:
        llm = self.require_llm()
        recent = "\n".join(f"- {h}" for h in history[-8:]) or "- (nothing yet)"
        prompt = (
            f"GOAL: {task.title}\n"
            f"TASK TYPE: {task.type}\n"
            f"WHY THIS IS A TASK: {task.why}\n\n"
            f"ACTIONS ALREADY TAKEN:\n{recent}\n\n"
            f"CURRENT PAGE:\n{snap.render()}"
        )
        try:
            raw: Any = await llm.json(SYSTEM, prompt)
        except Exception as exc:
            await self.warn(f"Planning failed: {exc}")
            return {"actions": [], "done": False, "blocked": str(exc)[:200]}

        if not isinstance(raw, dict):
            raw = {"actions": raw or [], "done": False}

        actions = [a for a in raw.get("actions", []) if isinstance(a, dict)][:4]
        valid = [a for a in actions if self._is_valid(a, snap)]
        if len(valid) != len(actions):
            await self.warn("Dropped action(s) referencing an element that does not exist.")

        return {
            "actions": valid,
            "done": bool(raw.get("done", False)),
            "blocked": str(raw.get("blocked", "")),
        }

    @staticmethod
    def _is_valid(action: dict[str, Any], snap: Snapshot) -> bool:
        if "index" not in action:
            return True
        try:
            return snap.find(int(action["index"])) is not None
        except (TypeError, ValueError):
            return False
