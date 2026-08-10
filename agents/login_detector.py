"""Decide *how* a site wants us to log in, then drive that flow.

Handles three shapes:
  form      — email + password fields on the page
  oneclick  — "Continue with Google / GitHub / wallet" style buttons
  none      — we are already authenticated (session restored)

OTP and CAPTCHA are never solved automatically. They escalate to the human
gate, which asks you on Telegram and waits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agents.base import Agent
from agents.browser_agent import BrowserAgent
from browser.dom import Snapshot

LOGGED_IN_HINTS = re.compile(
    r"\b(log ?out|sign ?out|keluar|my account|dashboard|profile|settings)\b", re.I
)
LOGIN_HINTS = re.compile(r"\b(log ?in|sign ?in|masuk|continue with|get started)\b", re.I)
OTP_HINTS = re.compile(r"\b(otp|one[- ]time|verification code|kode verifikasi|2fa|authenticator)\b", re.I)
CAPTCHA_HINTS = re.compile(r"\b(captcha|recaptcha|hcaptcha|verify you are human|robot)\b", re.I)


@dataclass(slots=True)
class LoginPlan:
    method: str  # none | form | oneclick | unknown
    email_index: int | None = None
    password_index: int | None = None
    submit_index: int | None = None
    provider_index: int | None = None
    provider_name: str = ""
    notes: list[str] = field(default_factory=list)


class LoginDetector(Agent):
    name = "login"

    def __init__(self, bus, state, browser: BrowserAgent, human_gate, llm=None) -> None:
        super().__init__(bus, state, llm)
        self.browser = browser
        self.gate = human_gate

    # ── detection ────────────────────────────────────────────────────────────
    @staticmethod
    def looks_authenticated(snap: Snapshot) -> bool:
        haystack = " ".join(e.label for e in snap.elements) + " " + snap.digest[:800]
        return bool(LOGGED_IN_HINTS.search(haystack)) and not _has_password_field(snap)

    async def detect(self, snap: Snapshot) -> LoginPlan:
        if self.looks_authenticated(snap):
            return LoginPlan(method="none", notes=["session already authenticated"])

        email_idx = password_idx = submit_idx = None
        for element in snap.elements:
            label = f"{element.label} {element.name} {element.id}".lower()
            if element.type == "password" and password_idx is None:
                password_idx = element.index
            elif (
                element.type in {"email", "text"}
                and email_idx is None
                and re.search(r"email|e-mail|user|login|phone|nomor", label)
            ):
                email_idx = element.index
            elif element.tag == "button" or element.type in {"submit", "button"}:
                if submit_idx is None and LOGIN_HINTS.search(element.label):
                    submit_idx = element.index

        if password_idx is not None and email_idx is not None:
            return LoginPlan("form", email_idx, password_idx, submit_idx)

        for element in snap.elements:
            match = re.search(
                r"continue with ([a-z ]+)|sign ?in with ([a-z ]+)|(google|github|apple|discord|metamask|wallet)",
                element.label,
                re.I,
            )
            if match:
                provider = next((g for g in match.groups() if g), "").strip()
                return LoginPlan("oneclick", provider_index=element.index, provider_name=provider)

        return LoginPlan("unknown", notes=["no recognisable login affordance"])

    # ── execution ────────────────────────────────────────────────────────────
    async def run(self, credentials: dict[str, str] | None = None) -> bool:
        snap = await self.browser.refresh()
        plan = await self.detect(snap)
        await self.info(f"Login strategy detected: {plan.method}")

        if plan.method == "none":
            self.state.logged_in = True
            self.state.login_method = "session"
            return True

        if plan.method == "form":
            creds = credentials or {}
            email = creds.get("email") or await self.gate.ask("Email / username for login?")
            password = creds.get("password") or await self.gate.ask(
                "Password? (sent only to the target site)", secret=True
            )
            if not email or not password:
                await self.fail("Login aborted — credentials not supplied.")
                return False
            await self.browser.execute({"action": "fill", "index": plan.email_index, "text": email})
            await self.browser.execute(
                {"action": "fill", "index": plan.password_index, "text": password, "secret": True}
            )
            if plan.submit_index is not None:
                await self.browser.execute({"action": "click", "index": plan.submit_index})
            else:
                await self.browser.execute({"action": "press", "key": "Enter"})
            self.state.login_method = "form"

        elif plan.method == "oneclick":
            await self.info(f"Using one-click provider: {plan.provider_name or 'unknown'}")
            await self.browser.execute({"action": "click", "index": plan.provider_index})
            self.state.login_method = f"oneclick:{plan.provider_name}"

        else:
            await self.warn("No login flow found — continuing as anonymous visitor.")
            return False

        return await self._resolve_challenges()

    async def _resolve_challenges(self, max_rounds: int = 4) -> bool:
        """Loop through OTP / CAPTCHA walls, escalating each to the human."""
        for _ in range(max_rounds):
            await self.browser.execute({"action": "wait", "ms": 2000})
            snap = await self.browser.refresh()
            text = snap.digest[:1500] + " ".join(e.label for e in snap.elements)

            if self.looks_authenticated(snap):
                self.state.logged_in = True
                await self.emit("done", "Authenticated.")
                await self.browser.driver.save_session()
                return True

            if CAPTCHA_HINTS.search(text):
                await self.emit("gate", "CAPTCHA detected — needs a human.")
                answer = await self.gate.ask(
                    "CAPTCHA blocking the login. Solve it in the browser, then reply 'done'."
                )
                if not answer:
                    return False
                continue

            if OTP_HINTS.search(text):
                await self.emit("gate", "OTP required — asking you for the code.")
                code = await self.gate.ask("Enter the OTP / verification code:")
                if not code:
                    return False
                otp_field = next(
                    (
                        e
                        for e in snap.elements
                        if e.tag in {"input"}
                        and e.type in {"text", "tel", "number", ""}
                        and not e.disabled
                    ),
                    None,
                )
                if otp_field:
                    await self.browser.execute(
                        {"action": "fill", "index": otp_field.index, "text": code, "secret": True}
                    )
                    await self.browser.execute({"action": "press", "key": "Enter"})
                continue

            # nothing recognisable changed — assume the flow finished or stalled
            break

        snap = await self.browser.refresh()
        self.state.logged_in = self.looks_authenticated(snap)
        if self.state.logged_in:
            await self.browser.driver.save_session()
            await self.emit("done", "Authenticated.")
        else:
            await self.warn("Could not confirm authentication.")
        return self.state.logged_in


def _has_password_field(snap: Snapshot) -> bool:
    return any(e.type == "password" for e in snap.elements)
