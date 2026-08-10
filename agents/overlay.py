"""Get the welcome dialog out of the way.

Observed on a real target: the landing page rendered exactly one button
("Hello, XIIID"), no links and no inputs, with `body { overflow: hidden }`.
The whole page was a welcome modal. Every downstream agent was therefore
reading the modal instead of the product — login detection found nothing,
and discovery had nowhere to go.

Cookie walls, age gates and onboarding dialogs all fail the same way, so this
runs before anything tries to interpret a page.
"""
from __future__ import annotations

import re

from agents.base import Agent
from agents.browser_agent import BrowserAgent
from browser.dom import Snapshot

# Affirmative controls that close an overlay. Deliberately excludes anything
# that could consent to something meaningful beyond dismissing a dialog.
DISMISS_RE = re.compile(
    r"\b(ok|okay|got it|understood|mengerti|continue|lanjut|lanjutkan|start|mulai|"
    r"begin|next|skip|lewati|later|nanti|close|tutup|dismiss|no thanks|"
    r"accept|terima|allow|agree|setuju|hello|halo|hi|done|selesai|explore|"
    r"jump in|let's go|get started)\b",
    re.I,
)
# Bare glyph close buttons.
GLYPH_RE = re.compile(r"^[\s×xX✕✖✗⨯]+$")

# Never press these while clearing an overlay.
AVOID_RE = re.compile(
    r"\b(delete|hapus|remove|pay|bayar|buy|subscribe|upgrade|reject all|"
    r"decline|sign ?up|register|daftar)\b",
    re.I,
)

MAX_ROUNDS = 3


class OverlayDismisser(Agent):
    name = "overlay"

    def __init__(self, bus, state, browser: BrowserAgent, llm=None) -> None:
        super().__init__(bus, state, llm)
        self.browser = browser

    @staticmethod
    def pick_dismiss_control(snap: Snapshot) -> int | None:
        """Choose the control most likely to close the overlay.

        Falls back to 'the only button on the page', which is precisely the
        single-button welcome dialog case.
        """
        clickable = [
            e
            for e in snap.elements
            if not e.disabled
            and (e.tag == "button" or e.role in {"button"} or e.type in {"button", "submit"})
        ]
        candidates = [e for e in clickable if not AVOID_RE.search(e.label)]

        for element in candidates:
            if GLYPH_RE.match(element.label or ""):
                return element.index
        for element in candidates:
            if DISMISS_RE.search(element.label or ""):
                return element.index
        # A page showing exactly one button and nothing else is a dialog.
        if len(candidates) == 1 and len(snap.elements) <= 3:
            return candidates[0].index
        return None

    async def run(self) -> bool:
        """Clear blocking overlays. Returns True if anything was dismissed."""
        dismissed = False

        for _round in range(MAX_ROUNDS):
            snap = await self.browser.current()
            if not snap.modal:
                break

            index = self.pick_dismiss_control(snap)
            if index is None:
                # Escape closes many dialogs and cannot click the wrong thing.
                await self.browser.execute({"action": "press", "key": "Escape"})
                snap = await self.browser.refresh()
                if not snap.modal:
                    dismissed = True
                    await self.info("Overlay closed with Escape.")
                    break
                await self.warn("An overlay is present but no safe way to close it was found.")
                break

            element = snap.find(index)
            label = element.label if element else f"#{index}"
            await self.info(f"Dismissing overlay via {label!r}")
            result = await self.browser.execute({"action": "click", "index": index})
            if not result.ok:
                break
            await self.browser.execute({"action": "wait", "ms": 1200})
            dismissed = True

        if dismissed:
            snap = await self.browser.refresh()
            await self.emit(
                "done",
                f"Overlay cleared — {len(snap.elements)} controls now visible.",
            )
        return dismissed
