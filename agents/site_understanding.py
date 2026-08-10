"""Understand the site before touching it.

This runs immediately after login and answers four questions the rest of the
pipeline depends on:

  1. What is this site for?
  2. What does it call its rewards? (its vocabulary, not ours)
  3. Which reward systems exist?
  4. Which areas are worth exploring, and which must be left alone?

Without this step, discovery degenerates into guessing at `/tasks` and
keyword-matching English words like "quest" — which silently misses any site
that calls its currency "Sparks" or writes its UI in Indonesian.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from agents.base import Agent
from browser.dom import Snapshot
from core.rewards import extract_counters

SYSTEM = """You are analysing a website that the user has legitimate access to.

Your goal:
"Understand this website and identify legitimate activities that can generate
points, XP, badges, rewards, or campaign progress — even when those activities
are NOT explicitly labelled as tasks, quests, or missions."

You are shown the landing page after login: its navigation, its interactive
elements and its visible text. Infer the shape of the product.

Think about:
- What the site is FOR (learning, loyalty, fitness, trading, community, ...).
- The words THIS site uses for its rewards. Copy its exact terms, whatever the
  language — "Sparks", "Bintang", "Gems", "Streak", "Tier", "Level".
- Which reward systems exist and where progress is displayed.
- Which navigation areas plausibly contain repeatable user activities. An
  activity can be a lesson to finish, a video to watch, a wheel to spin, a
  daily button to press, a form to submit, a streak to continue — it does not
  need the word "task" anywhere near it.
- Which areas to avoid entirely: billing, payments, account deletion, privacy
  settings, public posting.

Return JSON:
{
  "site_purpose": "one sentence, concrete",
  "site_type": "short category label",
  "reward_vocabulary": ["exact words this site uses for its currencies"],
  "reward_systems": [{"name": "...", "where": "url or ui location", "evidence": "..."}],
  "explore": [{"url": "absolute url from the page", "why": "...", "priority": 1}],
  "avoid": ["/billing", "/settings/privacy"],
  "confidence": 0.0-1.0
}

priority: 1 = most likely to contain activities, 5 = least.
Only use URLs that appear in the data you were given. Never invent one."""


class SiteUnderstanding(Agent):
    name = "understand"

    async def run(self, snap: Snapshot) -> dict[str, Any]:
        llm = self.require_llm()
        await self.info("Reading the site to work out what it actually is...")

        try:
            raw: Any = await llm.json(SYSTEM, snap.render(max_elements=100))
        except Exception as exc:
            await self.warn(f"Understanding failed, falling back to generic exploration: {exc}")
            raw = {}

        if not isinstance(raw, dict):
            raw = {}

        understanding = {
            "site_purpose": str(raw.get("site_purpose", ""))[:400],
            "site_type": str(raw.get("site_type", ""))[:80],
            "reward_vocabulary": _clean_words(raw.get("reward_vocabulary")),
            "reward_systems": _clean_systems(raw.get("reward_systems")),
            "explore": _clean_explore(raw.get("explore"), snap),
            "avoid": [str(a)[:120] for a in (raw.get("avoid") or [])][:20],
            "confidence": float(raw.get("confidence", 0.5) or 0.5),
        }

        self.state.understanding = understanding
        self.state.reward_baseline = extract_counters(
            snap.digest, understanding["reward_vocabulary"]
        )
        self.state.save()

        if understanding["site_purpose"]:
            await self.info(f"Site: {understanding['site_purpose']}")
        if understanding["reward_vocabulary"]:
            await self.info(
                "Reward vocabulary: " + ", ".join(understanding["reward_vocabulary"])
            )
        if self.state.reward_baseline:
            await self.info(
                "Baseline: "
                + ", ".join(f"{k}={v:g}" for k, v in sorted(self.state.reward_baseline.items()))
            )
        await self.emit(
            "done",
            f"Understanding complete — {len(understanding['explore'])} area(s) queued "
            f"for exploration.",
        )
        return understanding


def _clean_words(values: Any) -> list[str]:
    out: list[str] = []
    for value in values or []:
        word = str(value).strip()
        # single words / short phrases only; a sentence is not a currency name
        if word and len(word) <= 24 and word.lower() not in {w.lower() for w in out}:
            out.append(word)
    return out[:15]


def _clean_systems(values: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        out.append(
            {
                "name": str(value.get("name", ""))[:60],
                "where": str(value.get("where", ""))[:200],
                "evidence": str(value.get("evidence", ""))[:200],
            }
        )
    return out[:10]


def _clean_explore(values: Any, snap: Snapshot) -> list[dict[str, Any]]:
    """Keep only same-origin URLs that really appeared on the page."""
    origin = urlparse(snap.url).netloc
    seen_urls = {snap.url}
    for element in snap.links():
        seen_urls.add(urljoin(snap.url, element.href))

    out: list[dict[str, Any]] = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        url = urljoin(snap.url, str(value.get("url", "")).strip())
        if not url or urlparse(url).netloc != origin:
            continue
        if url not in seen_urls:
            continue  # hallucinated link
        out.append(
            {
                "url": url.rstrip("/"),
                "why": str(value.get("why", ""))[:200],
                "priority": max(1, min(5, int(value.get("priority", 3) or 3))),
            }
        )
    out.sort(key=lambda e: e["priority"])
    return out[:20]
