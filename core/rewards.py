"""Reward-counter extraction.

A site rarely calls its currency "points". It calls it Sparks, Bintang, Gems,
Streak, Tier. So the pattern is built at runtime from the vocabulary the
SiteUnderstanding agent learned, with a generic fallback list underneath.

These counters are the strongest completion signal we have: if the number went
up, the task worked — no model opinion required.
"""
from __future__ import annotations

import re

# Always recognised, whatever the site calls its own currency.
GENERIC_UNITS = [
    "xp", "exp", "point", "points", "poin", "coin", "coins", "credit", "credits",
    "gem", "gems", "star", "stars", "token", "tokens", "badge", "badges",
    "streak", "level", "tier", "rank", "score", "reward", "rewards",
]


def build_pattern(vocabulary: list[str] | None = None) -> re.Pattern[str]:
    words = {w.lower().strip() for w in (vocabulary or []) if w and w.strip()}
    words.update(GENERIC_UNITS)
    # longest first so "points" wins over "point"
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    # A duration word often sits between the number and the unit:
    # "7 day Streak", "30-day streak", "7 hari Streak".
    filler = r"(?:[-\s]\s*(?:days?|hari|weeks?|minggu|months?|bulan)\s+)?"
    return re.compile(
        rf"(?:(\d[\d,\.]*)\s*{filler}({alt})\b)"      # 120 XP / 7 day Streak
        rf"|(?:\b({alt})\s*[:\-]?\s*(\d[\d,\.]*))",   # XP: 120
        re.I,
    )


def _to_float(raw: str) -> float | None:
    cleaned = raw.replace(",", "").rstrip(".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_counters(text: str, vocabulary: list[str] | None = None) -> dict[str, float]:
    """Map every reward unit found in `text` to its highest observed value.

    Highest rather than last: a page often shows both "+10 XP" for an action
    and "1,240 XP" for the running total, and the total is what we track.
    """
    counters: dict[str, float] = {}
    for match in build_pattern(vocabulary).finditer(text or ""):
        number, unit = (match.group(1), match.group(2))
        if number is None:
            unit, number = match.group(3), match.group(4)
        if not number or not unit:
            continue
        value = _to_float(number)
        if value is None:
            continue
        key = unit.lower()
        counters[key] = max(counters.get(key, value), value)
    return counters


def diff_counters(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """Units that moved, and by how much."""
    return {
        unit: round(value - before[unit], 2)
        for unit, value in after.items()
        if unit in before and value != before[unit]
    }


def describe_delta(delta: dict[str, float]) -> str:
    if not delta:
        return ""
    return ", ".join(f"{unit} {value:+g}" for unit, value in sorted(delta.items()))
