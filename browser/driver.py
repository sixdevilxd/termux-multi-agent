"""Playwright lifecycle wrapper with a Termux-friendly CDP mode."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from config.settings import settings
from core.logger import get_logger
from browser.session import load_storage_state, save_storage_state

log = get_logger("browser")

UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)


class BrowserDriver:
    """Owns the Playwright instance, browser, context and active page.

    Two modes:
      cdp    — attach to a Chromium you started yourself (the only thing that
               works on Termux, where Playwright cannot ship its own binary)
      launch — Playwright launches Chromium itself (desktop Linux / VPS)
    """

    def __init__(self, target_url: str) -> None:
        self.target_url = target_url
        self._pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self._owns_browser = False

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        storage_state = load_storage_state(self.target_url)

        if settings.browser_mode == "cdp":
            log.info("Attaching to Chromium over CDP at %s", settings.cdp_url)
            self.browser = await self._pw.chromium.connect_over_cdp(settings.cdp_url)
            self._owns_browser = False
            contexts = self.browser.contexts
            self.context = contexts[0] if contexts else await self.browser.new_context()
            if storage_state:
                await self._restore_cookies(storage_state)
            pages = self.context.pages
            self.page = pages[0] if pages else await self.context.new_page()
        else:
            log.info("Launching Chromium (headless=%s)", settings.headless)
            self.browser = await self._pw.chromium.launch(
                headless=settings.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._owns_browser = True
            self.context = await self.browser.new_context(
                user_agent=UA_ANDROID,
                viewport={"width": 412, "height": 915},
                storage_state=storage_state,
            )
            self.page = await self.context.new_page()

        self.context.set_default_timeout(settings.nav_timeout_ms)
        self.page.set_default_timeout(settings.nav_timeout_ms)

    async def _restore_cookies(self, storage_state: dict) -> None:
        cookies = storage_state.get("cookies") or []
        if cookies:
            try:
                await self.context.add_cookies(cookies)
            except Exception as exc:  # malformed cookie jar should not be fatal
                log.warning("Could not restore cookies: %s", exc)

    async def save_session(self) -> None:
        if not self.context:
            return
        try:
            save_storage_state(self.target_url, await self.context.storage_state())
        except Exception as exc:
            log.warning("Could not save session: %s", exc)

    async def screenshot(self, name: str) -> Path | None:
        if not self.page:
            return None
        settings.ensure_dirs()
        path = settings.screenshots_dir / f"{name}.png"
        try:
            await self.page.screenshot(path=str(path), full_page=False)
            return path
        except Exception as exc:
            log.warning("Screenshot failed: %s", exc)
            return None

    async def stop(self) -> None:
        await self.save_session()
        try:
            if self._owns_browser and self.browser:
                await self.browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
        log.info("Browser stopped.")

    async def __aenter__(self) -> "BrowserDriver":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()
