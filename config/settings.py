"""Central configuration, loaded once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


def _csv(key: str) -> list[str]:
    raw = os.getenv(key, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}


@dataclass(frozen=True)
class Settings:
    # telegram
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_allowed_users: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(u) for u in _csv("TELEGRAM_ALLOWED_USERS") if u.lstrip("-").isdigit()
        )
    )

    # llm
    llm_provider: str = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()
    llm_model: str = os.getenv("LLM_MODEL", "anthropic/claude-3.5-sonnet")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")

    # browser
    browser_mode: str = os.getenv("BROWSER_MODE", "cdp").strip().lower()
    cdp_url: str = os.getenv("CDP_URL", "http://127.0.0.1:9222")
    headless: bool = _bool("HEADLESS", True)
    nav_timeout_ms: int = _int("NAV_TIMEOUT_MS", 30_000)

    # safety
    dry_run: bool = _bool("DRY_RUN", False)
    max_actions: int = _int("MAX_ACTIONS", 120)
    max_discovery_pages: int = _int("MAX_DISCOVERY_PAGES", 25)
    max_discovery_depth: int = _int("MAX_DISCOVERY_DEPTH", 3)
    human_gate_timeout: int = _int("HUMAN_GATE_TIMEOUT", 600)

    # storage
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @property
    def resolved_base_url(self) -> str:
        return self.llm_base_url or DEFAULT_BASE_URLS.get(
            self.llm_provider, DEFAULT_BASE_URLS["openrouter"]
        )

    @property
    def sessions_dir(self) -> Path:
        return self.storage_dir / "sessions"

    @property
    def runs_dir(self) -> Path:
        return self.storage_dir / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.storage_dir / "reports"

    @property
    def screenshots_dir(self) -> Path:
        return self.storage_dir / "screenshots"

    def ensure_dirs(self) -> None:
        for d in (
            self.sessions_dir,
            self.runs_dir,
            self.reports_dir,
            self.screenshots_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        """Return a list of human-readable configuration problems."""
        problems: list[str] = []
        if not self.llm_api_key:
            problems.append("LLM_API_KEY is empty — the reasoning agents cannot run.")
        if self.browser_mode not in {"cdp", "launch"}:
            problems.append(f"BROWSER_MODE must be 'cdp' or 'launch', got {self.browser_mode!r}.")
        if self.llm_provider not in DEFAULT_BASE_URLS:
            problems.append(f"Unknown LLM_PROVIDER {self.llm_provider!r}.")
        return problems


settings = Settings()
