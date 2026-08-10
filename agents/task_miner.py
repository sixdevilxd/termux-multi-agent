"""Turn the site map into a classified, prioritised task list.

The hard part is not finding things labelled "quest". It is recognising that
a lesson with a Continue button, a wheel you can spin once a day, or a form
that says "submit to earn 5 Sparks" are all the same kind of thing.
"""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from agents.base import Agent
from core.state import PageInfo, Task

VALID_TYPES = {
    "quiz", "quest", "checkin", "claim", "survey", "lesson", "watch",
    "game", "streak", "referral", "other",
}

SYSTEM = """You are a task-mining analyst for a web automation system.

Goal:
"Identify legitimate activities on this website that generate points, XP,
badges, rewards or campaign progress — INCLUDING activities that are not
labelled as tasks, quests or missions."

You receive a map of pages the user is logged into: URLs, titles, links and
visible text, plus the vocabulary this site uses for its own rewards.

An activity qualifies if a logged-in user can DO something repeatable that
moves a reward counter or progress bar. Examples of things people miss:
- a lesson or module with a Continue / Start button
- a daily button: check in, claim, collect, spin, open
- a video or article marked as unwatched / unread
- a form, poll or survey that grants progress on submit
- a streak that continues by performing today's action
- a level or tier that fills as you act

Rules:
- Ground every task in evidence from the supplied data. Quote it in "why".
- Use a URL that appears in the map. Never invent one.
- Ignore anything destructive, financial, or that posts publicly.
- Ignore navigation, marketing, docs, settings and legal pages.
- Do NOT pad the list. If the site has no such activities, return [].

Classify and rank each one:
  type       one of: quiz, quest, checkin, claim, survey, lesson, watch,
             game, streak, referral, other
  priority   1 = do first (cheap, clearly rewarded, repeatable)
             5 = do last (unclear payoff or heavy)
  effort     low | medium | high
  reward     what the site says you get, in ITS words ("50 Sparks"), or ""
  confidence 0.0-1.0 that this is a real, completable activity

Return JSON:
{"tasks": [{"type":..., "title":..., "url":..., "why":..., "reward":...,
            "priority":1, "effort":"low", "confidence":0.8}]}"""


class TaskMiner(Agent):
    name = "miner"

    async def run(self, pages: list[PageInfo]) -> list[Task]:
        if not pages:
            await self.warn("No pages to mine.")
            return []

        llm = self.require_llm()
        understanding = self.state.understanding
        vocab = self.state.vocabulary
        await self.info(f"Mining {len(pages)} pages for activities...")

        header = (
            f"SITE PURPOSE: {understanding.get('site_purpose', 'unknown')}\n"
            f"SITE TYPE: {understanding.get('site_type', 'unknown')}\n"
            f"REWARD VOCABULARY: {', '.join(vocab) if vocab else 'unknown'}\n"
            f"REWARD SYSTEMS: "
            + "; ".join(
                f"{s.get('name')} ({s.get('where')})"
                for s in understanding.get("reward_systems", [])
            )
            + "\n\n"
        )
        payload = header + "\n\n".join(self._render_page(p) for p in pages[:25])

        try:
            raw: Any = await llm.json(SYSTEM, payload)
        except Exception as exc:
            await self.fail(f"Task mining failed: {exc}")
            return []

        items = raw.get("tasks", []) if isinstance(raw, dict) else (raw or [])
        tasks = self._build(items, pages)

        tasks.sort(key=lambda t: t.rank())
        self.state.tasks = tasks
        self.state.save()

        if tasks:
            spread = ", ".join(
                f"{t}×{sum(1 for x in tasks if x.type == t)}"
                for t in sorted({x.type for x in tasks})
            )
            await self.info(f"Types found: {spread}")
        await self.emit("done", f"Found {len(tasks)} candidate task(s).")
        return tasks

    # ── parsing ──────────────────────────────────────────────────────────────
    def _build(self, items: Any, pages: list[PageInfo]) -> list[Task]:
        known = {p.url.rstrip("/") for p in pages}
        origin = urlparse(pages[0].url).netloc
        tasks: list[Task] = []
        seen: set[tuple[str, str]] = set()

        for item in items or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip().rstrip("/")
            title = str(item.get("title", "")).strip()
            if not url or not title:
                continue
            if urlparse(url).netloc != origin:
                continue
            if url not in known and not any(url in p or p in url for p in known):
                self.log.warning("Dropping hallucinated URL: %s", url)
                continue

            key = (title.lower(), url)
            if key in seen:
                continue
            seen.add(key)

            task_type = str(item.get("type", "other")).lower().strip()
            tasks.append(
                Task(
                    id=uuid.uuid4().hex[:8],
                    type=task_type if task_type in VALID_TYPES else "other",
                    title=title[:120],
                    url=url,
                    why=str(item.get("why", ""))[:300],
                    reward=str(item.get("reward", ""))[:80],
                    priority=_clamp_int(item.get("priority"), 1, 5, 3),
                    effort=_effort(item.get("effort")),
                    confidence=_clamp_float(item.get("confidence"), 0.0, 1.0, 0.5),
                )
            )
        return tasks

    @staticmethod
    def _render_page(page: PageInfo) -> str:
        links = "\n".join(f"  - {l['label']!r} -> {l['url']}" for l in page.nav_links[:20])
        return (
            f"PAGE: {page.url}\n"
            f"TITLE: {page.title}\n"
            f"LINKS:\n{links or '  (none)'}\n"
            f"TEXT: {page.text_digest[:700]}"
        )


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _effort(value: Any) -> str:
    text = str(value or "").lower().strip()
    return text if text in {"low", "medium", "high"} else ""
