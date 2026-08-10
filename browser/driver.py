"""Playwright lifecycle wrapper with a Termux-friendly CDP mode."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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

    def set_page(self, page: Any) -> None:
        """Switch the active page (used when an OAuth popup opens)."""
        self.page = page

    async def focus_latest_page(self) -> None:
        if not self.context:
            return
        pages = [p for p in self.context.pages if not p.is_closed()]
        if pages:
            self.page = pages[-1]
            try:
                await self.page.bring_to_front()
            except Exception:
                pass

    async def focus_page_for_host(self, host: str) -> None:
        """Prefer a non-closed page whose URL matches host; else latest page."""
        if not self.context:
            return
        host = (host or "").lower().removeprefix("www.")
        pages = [p for p in self.context.pages if not p.is_closed()]
        if not pages:
            return
        chosen = None
        for page in reversed(pages):
            try:
                page_host = urlparse(page.url).netloc.lower().removeprefix("www.")
            except Exception:
                continue
            if not host or page_host == host or page_host.endswith("." + host):
                chosen = page
                break
        self.page = chosen or pages[-1]
        try:
            await self.page.bring_to_front()
        except Exception:
            pass

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
            launch_args = ["--no-sandbox", "--disable-dev-shm-usage", *settings.browser_args]
            launch_kwargs: dict[str, Any] = {
                "headless": settings.headless,
                "args": launch_args,
            }
            if settings.chrome_path:
                # Drive a system Chromium (Debian/proot, or any distro package)
                # instead of a Playwright-managed download.
                launch_kwargs["executable_path"] = settings.chrome_path
                log.info("Launching system Chromium at %s", settings.chrome_path)
            else:
                log.info("Launching Playwright-managed Chromium")
            log.info("headless=%s args=%s", settings.headless, " ".join(launch_args))

            self.browser = await self._pw.chromium.launch(**launch_kwargs)
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
            # Prefer a page on the target host so storage_state captures the right jar.
            target_host = urlparse(self.target_url).netloc.lower().removeprefix("www.")
            await self.focus_page_for_host(target_host)
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
