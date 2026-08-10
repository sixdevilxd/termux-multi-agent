"""Breadth-first exploration of the authenticated app.

Produces the map that the Task Miner reasons over: which pages exist, what
each one is called, and what is clickable on it.
"""
from __future__ import annotations

from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from agents.base import Agent
from agents.browser_agent import BrowserAgent
from config.settings import settings
from core.guardrails import Guardrails
from core.state import PageInfo

# Pages that are never worth crawling for tasks.
SKIP_PATTERNS = (
    "/logout", "/signout", "/sign-out", "/delete", "/billing", "/checkout",
    "/payment", "/privacy", "/terms", "/legal", "/cookie", ".pdf", ".zip",
    "mailto:", "tel:", "javascript:",
)

# Words that suggest a page is worth prioritising in the queue.
INTERESTING = (
    "dashboard", "quest", "task", "mission", "reward", "point", "xp", "level",
    "challenge", "game", "learn", "course", "quiz", "daily", "earn", "badge",
    "achievement", "profile", "explore", "activity", "streak",
)


def _normalise(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    url, _ = urldefrag(urljoin(base, href))
    return url.rstrip("/") or url


def _score(url: str, label: str) -> int:
    haystack = f"{url} {label}".lower()
    return sum(2 for word in INTERESTING if word in haystack)


class DiscoveryAgent(Agent):
    name = "discovery"

    def __init__(self, bus, state, browser: BrowserAgent, guardrails: Guardrails, llm=None) -> None:
        super().__init__(bus, state, llm)
        self.browser = browser
        self.guardrails = guardrails

    async def run(self, start_url: str) -> list[PageInfo]:
        max_pages = settings.max_discovery_pages
        max_depth = settings.max_discovery_depth

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start_url.rstrip("/"), 0)])
        pages: list[PageInfo] = []

        await self.info(f"Exploring up to {max_pages} pages (depth {max_depth}).")

        while queue and len(pages) < max_pages:
            url, depth = queue.popleft()
            if url in visited or depth > max_depth:
                continue
            if any(p in url.lower() for p in SKIP_PATTERNS):
                continue
            if not self.guardrails.check_url(url):
                continue
            visited.add(url)

            result = await self.browser.execute({"action": "goto", "url": url})
            if not result.ok:
                await self.warn(f"Skipping unreachable page: {url}")
                continue

            snap = await self.browser.refresh()
            links: list[dict[str, str]] = []
            candidates: list[tuple[int, str]] = []

            for element in snap.elements:
                if not element.href:
                    continue
                child = _normalise(snap.url, element.href)
                if not child or urlparse(child).netloc != urlparse(snap.url).netloc:
                    continue
                links.append({"label": element.label, "url": child})
                if child not in visited:
                    candidates.append((_score(child, element.label), child))

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

            # highest-signal pages first
            for _, child in sorted(candidates, key=lambda c: -c[0])[:12]:
                queue.append((child, depth + 1))

        self.state.pages = pages
        self.state.save()
        await self.emit("done", f"Discovery finished — {len(pages)} pages mapped.")
        return pages
