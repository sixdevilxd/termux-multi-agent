"""Run state — the single source of truth passed between agents.

Persisted to disk after every phase so a crashed run can be inspected (and,
later, resumed) instead of silently disappearing.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PageInfo:
    url: str
    title: str = ""
    depth: int = 0
    nav_links: list[dict[str, str]] = field(default_factory=list)
    text_digest: str = ""


@dataclass
class Task:
    id: str
    type: str  # quiz | quest | checkin | claim | survey | lesson | other
    title: str
    url: str
    why: str = ""
    confidence: float = 0.0
    priority: int = 3  # 1 = do first, 5 = do last
    effort: str = ""  # low | medium | high
    reward: str = ""  # what the site says you get
    status: str = "pending"  # pending | planning | running | verified | failed | skipped
    attempts: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def rank(self) -> tuple[int, float]:
        """Sort key: priority first, confidence breaks ties."""
        return (self.priority, -self.confidence)


@dataclass
class RunState:
    target_url: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(default_factory=_now)
    finished_at: str = ""
    phase: str = "created"
    logged_in: bool = False
    login_method: str = ""
    actions_used: int = 0
    # What the site actually is, in the agent's own words. Written by the
    # SiteUnderstanding agent and used to steer discovery and mining.
    understanding: dict[str, Any] = field(default_factory=dict)
    # Reward counters read from the site's own vocabulary, before and after.
    reward_baseline: dict[str, float] = field(default_factory=dict)
    reward_final: dict[str, float] = field(default_factory=dict)
    pages: list[PageInfo] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def vocabulary(self) -> list[str]:
        """Site-specific reward words, e.g. ['Sparks', 'Streak', 'Tier']."""
        return [str(w) for w in self.understanding.get("reward_vocabulary", []) if w]

    def reward_delta(self) -> dict[str, float]:
        return {
            k: round(v - self.reward_baseline.get(k, 0.0), 2)
            for k, v in self.reward_final.items()
            if v != self.reward_baseline.get(k)
        }
    @property
    def path(self) -> Path:
        return settings.runs_dir / f"{self.run_id}.json"

    def task_by_id(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tasks:
            out[t.status] = out.get(t.status, 0) + 1
        return out

    def budget_left(self) -> int:
        return max(0, settings.max_actions - self.actions_used)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self) -> Path:
        settings.ensure_dirs()
        self.path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, run_id: str) -> "RunState":
        raw = json.loads((settings.runs_dir / f"{run_id}.json").read_text(encoding="utf-8"))
        pages = [PageInfo(**p) for p in raw.pop("pages", [])]
        tasks = [Task(**t) for t in raw.pop("tasks", [])]
        state = cls(**raw)
        state.pages = pages
        state.tasks = tasks
        return state
