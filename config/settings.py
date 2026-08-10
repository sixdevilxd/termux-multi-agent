"""Central configuration, loaded once from the environment."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

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
    # Write the host on its own — the client appends the right API path.
    # China mainland fallback: ps.air-outer.com
    "agentrouter": "agentrouter.org",
}

# Providers that speak the OpenAI /chat/completions wire format.
OPENAI_COMPATIBLE = {"openai", "openrouter", "agentrouter"}

# Providers that are a local process rather than an HTTP endpoint. They need no
# API key and no base URL — whatever the CLI is already configured with is used.
LOCAL_PROVIDERS = {"claude_cli"}

ALL_PROVIDERS = set(DEFAULT_BASE_URLS) | LOCAL_PROVIDERS


def normalise_base_url(url: str) -> str:
    """Accept `agentrouter.org`, `https://agentrouter.org/`, `.../v1` alike."""
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


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
    llm_provider: str = os.getenv("LLM_PROVIDER", "agentrouter").strip().lower()
    llm_model: str = os.getenv("LLM_MODEL", "claude-opus-5")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")

    # claude_cli provider: delegate generation to the Claude Code CLI. Useful
    # when a gateway only whitelists known client apps — the request is then
    # genuinely made by Claude Code, with whatever ANTHROPIC_BASE_URL and
    # ANTHROPIC_AUTH_TOKEN that CLI is already configured with.
    claude_bin: str = os.getenv("CLAUDE_BIN", "claude").strip() or "claude"
    claude_timeout: int = _int("CLAUDE_TIMEOUT", 180)

    # Optional stored credentials for the target site. They go straight into
    # the vault at run time, so they never reach a model, a log or the report.
    # LOGIN_DOMAIN binds them to one host: without it, anyone who can send
    # /run <url> could have them typed into a site of their choosing.
    login_email: str = os.getenv("LOGIN_EMAIL", "").strip()
    login_password: str = os.getenv("LOGIN_PASSWORD", "")
    login_domain: str = os.getenv("LOGIN_DOMAIN", "")

    # browser
    browser_mode: str = os.getenv("BROWSER_MODE", "cdp").strip().lower()
    cdp_url: str = os.getenv("CDP_URL", "http://127.0.0.1:9222")
    # Path to a system Chromium. Set this when BROWSER_MODE=launch so Playwright
    # drives an already-installed browser instead of downloading its own —
    # which it cannot do on Android, and need not do inside Debian/proot.
    chrome_path: str = os.getenv("CHROME_PATH", "").strip()
    # Extra Chromium flags, comma separated. proot sometimes needs
    # --single-process,--no-zygote to start at all.
    browser_args: tuple[str, ...] = field(default_factory=lambda: tuple(_csv("BROWSER_ARGS")))
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
    def has_stored_credentials(self) -> bool:
        return bool(self.login_email and self.login_password)

    @property
    def login_host(self) -> str:
        """LOGIN_DOMAIN reduced to a bare host, however it was written."""
        raw = self.login_domain.strip().lower()
        if not raw:
            return ""
        if "//" in raw:
            raw = urlparse(normalise_base_url(raw)).netloc
        return raw.strip("/").removeprefix("www.")

    def credentials_for(self, url: str) -> dict[str, str]:
        """Stored credentials, but only for the host they were bound to."""
        if not self.has_stored_credentials:
            return {}
        bound = self.login_host
        if bound:
            host = urlparse(normalise_base_url(url)).netloc.lower().removeprefix("www.")
            if host != bound and not host.endswith("." + bound):
                return {}
        return {"email": self.login_email, "password": self.login_password}

    @property
    def is_local_provider(self) -> bool:
        return self.llm_provider in LOCAL_PROVIDERS

    @property
    def resolved_base_url(self) -> str:
        """The host the client talks to, without any API path appended."""
        if self.is_local_provider:
            return ""
        return normalise_base_url(
            self.llm_base_url
            or DEFAULT_BASE_URLS.get(self.llm_provider, DEFAULT_BASE_URLS["agentrouter"])
        )

    @property
    def is_openai_compatible(self) -> bool:
        return self.llm_provider in OPENAI_COMPATIBLE

    @property
    def api_root(self) -> str:
        """Base URL plus the version segment the provider's API lives under.

        Gemini pins its own version. Everything else is versioned `/v1`, which
        we add only when the configured URL does not already carry it — so
        `agentrouter.org` and `https://agentrouter.org/v1` both work.
        """
        base = self.resolved_base_url
        if not base:
            return ""
        if self.llm_provider == "gemini":
            return base
        return base if base.endswith("/v1") else base + "/v1"

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

        if self.is_local_provider:
            if not shutil.which(self.claude_bin):
                problems.append(
                    f"LLM_PROVIDER={self.llm_provider} needs the {self.claude_bin!r} "
                    "command on PATH. Install it with "
                    "`npm install -g @anthropic-ai/claude-code`, or set CLAUDE_BIN "
                    "to its full path."
                )
        else:
            if not self.llm_api_key:
                problems.append("LLM_API_KEY is empty — the reasoning agents cannot run.")
            if self.llm_provider not in ALL_PROVIDERS:
                problems.append(
                    f"Unknown LLM_PROVIDER {self.llm_provider!r}. "
                    f"Expected one of: {', '.join(sorted(ALL_PROVIDERS))}."
                )

        if self.browser_mode not in {"cdp", "launch"}:
            problems.append(f"BROWSER_MODE must be 'cdp' or 'launch', got {self.browser_mode!r}.")
        if self.chrome_path and not Path(self.chrome_path).exists():
            problems.append(
                f"CHROME_PATH points at {self.chrome_path!r}, which does not exist. "
                "Run `which chromium` and use that path."
            )
        if self.browser_mode == "launch" and not self.chrome_path:
            problems.append(
                "BROWSER_MODE=launch without CHROME_PATH means Playwright must supply its "
                "own browser. That works on desktop Linux (after `playwright install "
                "chromium`) but not on Android — set CHROME_PATH to a system Chromium."
            )
        return problems

    def warnings(self) -> list[str]:
        """Non-blocking notes: legal configurations that are usually mistakes."""
        notes: list[str] = []
        if self.llm_base_url and not self.is_local_provider:
            default = DEFAULT_BASE_URLS.get(self.llm_provider, "")
            default_host = urlparse(normalise_base_url(default)).netloc
            actual_host = urlparse(self.resolved_base_url).netloc
            if default_host and actual_host and actual_host != default_host:
                notes.append(
                    f"LLM_BASE_URL points at {actual_host}, but LLM_PROVIDER="
                    f"{self.llm_provider} normally uses {default_host}. "
                    "If you switched providers, clear LLM_BASE_URL."
                )
        if self.has_stored_credentials and not self.login_host:
            notes.append(
                "LOGIN_EMAIL/LOGIN_PASSWORD are set but LOGIN_DOMAIN is empty — they "
                "will be typed into whatever site is passed to /run. Set LOGIN_DOMAIN "
                "to the host they belong to."
            )
        return notes


settings = Settings()
