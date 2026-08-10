"""Goal-driven exploration.

Not "crawl until the budget runs out", and not "look for /tasks". The frontier
is ordered by what the SiteUnderstanding agent learned this specific site calls
its rewards, and it is re-ranked by the model a bounded number of times as the
picture fills in.

That distinction matters: a hardcoded English keyword list silently misses a
site whose currency is "Sparks", whose lessons live under /belajar, or whose
daily action is a button labelled "Ambil".
"""
from __future__ import annotations

from collections import deque
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from agents.base import Agent
from agents.browser_agent import BrowserAgent
from config.settings import settings
from core.guardrails import Guardrails
from core.state import PageInfo

# Never worth crawling, on any site.
SKIP_PATTERNS = (
    "/logout", "/signout", "/sign-out", "/delete", "/billing", "/checkout",
    "/payment", "/privacy", "/terms", "/legal", "/cookie", ".pdf", ".zip",
    "mailto:", "tel:", "javascript:",
)

# Weak, language-agnostic fallback used only until the site teaches us better.
GENERIC_HINTS = (
    "dashboard", "home", "explore", "activity", "profile", "learn", "play",
    "earn", "daily", "progress", "achievement", "belajar", "misi", "hadiah",
)

REPLAN_EVERY = 8      # pages between model re-rankings
MAX_REPLANS = 2       # hard cap on how much reasoning discovery may buy

REPLAN_SYSTEM = """You are steering a web crawler that is looking for
repeatable user activities which grant rewards — including ones not labelled
as tasks, quests or missions.

You get: what the site is, the pages already visited, and the unvisited links
currently queued. Choose which links are worth visiting next.

Prefer links likely to lead to: things to complete, claim, play, watch, answer,
continue or check in on. Skip settings, docs, marketing, legal and profile
pages unless progress is displayed there.

Return JSON: {"visit": ["url", "url", ...]} — at most 12, best first, chosen
only from the queued links you were shown."""


def _normalise(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    url, _ = urldefrag(urljoin(base, href))
    return url.rstrip("/") or url


class DiscoveryAgent(Agent):
    name = "discovery"

    def __init__(self, bus, state, browser: BrowserAgent, guardrails: Guardrails, llm=None) -> None:
        super().__init__(bus, state, llm)
        self.browser = browser
        self.guardrails = guardrails
        self._replans = 0

    # ── scoring ──────────────────────────────────────────────────────────────
    def _score(self, url: str, label: str) -> int:
        haystack = f"{url} {label}".lower()
        # what this site actually calls its rewards, learned at runtime
        score = sum(4 for word in self.state.vocabulary if word.lower() in haystack)
        score += sum(1 for word in GENERIC_HINTS if word in haystack)
        return score

    def _is_avoided(self, url: str) -> bool:
        lowered = url.lower()
        if any(pattern in lowered for pattern in SKIP_PATTERNS):
            return True
        return any(
            str(rule).lower().strip("/") in lowered
            for rule in self.state.understanding.get("avoid", [])
            if str(rule).strip("/")
        )

    # ── main loop ────────────────────────────────────────────────────────────
    async def run(self, start_url: str) -> list[PageInfo]:
        max_pages = settings.max_discovery_pages
        max_depth = settings.max_discovery_depth

        visited: set[str] = set()
        pages: list[PageInfo] = []
        # (url, depth); seeded from the understanding agent's shortlist
        queue: deque[tuple[str, int]] = deque()
        for entry in self.state.understanding.get("explore", []):
            queue.append((entry["url"], 1))
        queue.appendleft((start_url.rstrip("/"), 0))

        # unvisited candidates, kept for re-ranking: url -> (score, label)
        frontier: dict[str, tuple[int, str]] = {}

        vocab = self.state.vocabulary
        await self.info(
            f"Exploring up to {max_pages} pages"
            + (f", steered by: {', '.join(vocab)}" if vocab else " (no learned vocabulary yet)")
        )

        while len(pages) < max_pages:
            if not queue:
                if not frontier:
                    break
                queue.extend(self._drain_frontier(frontier, visited))
                if not queue:
                    break

            url, depth = queue.popleft()
            if url in visited or depth > max_depth or self._is_avoided(url):
                continue
            if not self.guardrails.check_url(url):
                continue
            visited.add(url)
            frontier.pop(url, None)

            result = await self.browser.execute({"action": "goto", "url": url})
            if not result.ok:
                await self.warn(f"Skipping unreachable page: {url}")
                continue

            snap = await self.browser.refresh()
            links: list[dict[str, str]] = []
            for element in snap.links():
                child = _normalise(snap.url, element.href)
                if not child or urlparse(child).netloc != urlparse(snap.url).netloc:
                    continue
                links.append({"label": element.label, "url": child})
                if child not in visited and not self._is_avoided(child):
                    score = self._score(child, element.label)
                    known = frontier.get(child)
                    if known is None or score > known[0]:
                        frontier[child] = (score, element.label)

            pages.append(
                PageInfo(
                    url=snap.url,
                    title=snap.title,
                    depth=depth,
                    nav_links=links[:40],
                    text_digest=snap.digest[:1200],
                )
            )
            await self.step(f"[{len(pages)}/{max_pages}] {snap.title or snap.url}")

            # periodically let the model redirect the crawl
            if (
                len(pages) % REPLAN_EVERY == 0
                and self._replans < MAX_REPLANS
                and frontier
                and self.llm is not None
            ):
                chosen = await self._replan(pages, frontier)
                if chosen:
                    queue.extendleft((u, depth + 1) for u in reversed(chosen))

            if not queue:
                queue.extend(self._drain_frontier(frontier, visited, depth + 1))

        self.state.pages = pages
        self.state.save()
        await self.emit("done", f"Discovery finished — {len(pages)} pages mapped.")
        return pages

    @staticmethod
    def _drain_frontier(
        frontier: dict[str, tuple[int, str]],
        visited: set[str],
        depth: int = 1,
        limit: int = 12,
    ) -> list[tuple[str, int]]:
        ranked = sorted(
            ((url, meta) for url, meta in frontier.items() if url not in visited),
            key=lambda item: -item[1][0],
        )
        return [(url, depth) for url, _ in ranked[:limit]]

    async def _replan(
        self, pages: list[PageInfo], frontier: dict[str, tuple[int, str]]
    ) -> list[str]:
        self._replans += 1
        candidates = sorted(frontier.items(), key=lambda item: -item[1][0])[:40]
        prompt = (
            f"SITE: {self.state.understanding.get('site_purpose', 'unknown')}\n"
            f"REWARD WORDS: {', '.join(self.state.vocabulary) or 'unknown'}\n\n"
            "ALREADY VISITED:\n"
            + "\n".join(f"- {p.title or p.url} ({p.url})" for p in pages[-15:])
            + "\n\nQUEUED LINKS:\n"
            + "\n".join(f"- {label!r} -> {url}" for url, (_, label) in candidates)
        )
        try:
            raw: Any = await self.llm.json(REPLAN_SYSTEM, prompt)
        except Exception as exc:
            await self.warn(f"Frontier re-ranking failed, keeping heuristic order: {exc}")
            return []

        allowed = {url for url, _ in candidates}
        chosen = [u for u in (raw.get("visit", []) if isinstance(raw, dict) else raw or [])
                  if isinstance(u, str) and u in allowed][:12]
        if chosen:
            await self.info(f"Re-ranked exploration — next {len(chosen)} page(s) chosen by model.")
        return chosen
