"""Turn the discovered site map into a concrete list of actionable tasks."""
from __future__ import annotations

import uuid
from typing import Any

from agents.base import Agent
from core.state import PageInfo, Task

SYSTEM = """You are a task-mining analyst for a web automation system.

You receive a map of pages from a website the user has legitimate access to.
Your job: identify concrete, repeatable activities a logged-in user can
complete on this site — quizzes, daily check-ins, quests, missions,
challenges, claimable rewards, surveys, lessons.

Rules:
- Only list activities you can point at a real URL for.
- Ignore anything destructive, financial, or that posts publicly.
- Ignore pure navigation, marketing pages, docs and settings.
- If the site has no such activities, return an empty list. Do not invent any.

Return JSON:
{"tasks": [
  {"type": "quiz|quest|checkin|claim|survey|lesson|other",
   "title": "short human label",
   "url": "absolute url from the map",
   "why": "the specific evidence in the page data that made you pick this",
   "confidence": 0.0-1.0}
]}"""


class TaskMiner(Agent):
    name = "miner"

    async def run(self, pages: list[PageInfo]) -> list[Task]:
        if not pages:
            await self.warn("No pages to mine.")
            return []

        llm = self.require_llm()
        await self.info(f"Mining {len(pages)} pages for activities...")

        payload = "\n\n".join(self._render_page(p) for p in pages[:25])
        try:
            raw: Any = await llm.json(SYSTEM, payload)
        except Exception as exc:
            await self.fail(f"Task mining failed: {exc}")
            return []

        items = raw.get("tasks", []) if isinstance(raw, dict) else (raw or [])
        known_urls = {p.url for p in pages}

        tasks: list[Task] = []
        for item in items:
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            # keep the model honest: URL must come from what we actually saw
            if url not in known_urls and not any(url in p.url or p.url in url for p in pages):
                await self.warn(f"Dropping hallucinated URL: {url}")
                continue
            tasks.append(
                Task(
                    id=uuid.uuid4().hex[:8],
                    type=str(item.get("type", "other")).lower(),
                    title=str(item.get("title", "Untitled"))[:120],
                    url=url,
                    why=str(item.get("why", ""))[:300],
                    confidence=float(item.get("confidence", 0.5) or 0.5),
                )
            )

        tasks.sort(key=lambda t: -t.confidence)
        self.state.tasks = tasks
        self.state.save()
        await self.emit("done", f"Found {len(tasks)} candidate task(s).")
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
