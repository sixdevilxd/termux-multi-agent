"""Per-domain session persistence so we do not re-login on every run."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from config.settings import settings
from core.logger import get_logger

log = get_logger("session")


def _slug(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.") or "unknown"
    return "".join(c if c.isalnum() or c in "-." else "_" for c in host)


def session_path(url: str) -> Path:
    settings.ensure_dirs()
    return settings.sessions_dir / f"{_slug(url)}.json"


def load_storage_state(url: str) -> dict | None:
    path = session_path(url)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        log.info("Reusing saved session for %s", _slug(url))
        return state
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read session %s: %s", path.name, exc)
        return None


def save_storage_state(url: str, state: dict) -> Path:
    path = session_path(url)
    path.write_text(json.dumps(state), encoding="utf-8")
    try:
        path.chmod(0o600)  # cookies are credentials
    except OSError:
        pass
    log.info("Saved session -> %s", path.name)
    return path


def clear_session(url: str) -> bool:
    path = session_path(url)
    if path.exists():
        path.unlink()
        return True
    return False
