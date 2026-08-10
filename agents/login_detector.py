"""Decide *how* a site wants us to log in, then drive that flow.

Handles four shapes:
  form      — email + password fields on the page
  oneclick  — "Continue with Google / GitHub / wallet" style buttons
  manual    — human completes an SSO / popup / unknown UI in the browser
  none      — we are already authenticated (session restored)

OTP and CAPTCHA are never solved automatically. They escalate to the human
gate, which asks you on Telegram and waits.

Google / Microsoft / Apple / GitHub OAuth is *not* automated. Those providers
actively block headless and scripted browsers. The agent opens the provider
button (when safe), switches into any OAuth popup, then asks you to finish
the flow and reply `done`. The resulting site session is saved for later runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from agents.base import Agent
from agents.browser_agent import BrowserAgent
from browser.dom import Snapshot

LOGGED_IN_HINTS = re.compile(
    r"\b(log ?out|sign ?out|keluar|my account|dashboard|profile|settings)\b", re.I
)
LOGIN_HINTS = re.compile(r"\b(log ?in|sign ?in|masuk|continue with|get started)\b", re.I)
# A landing page often only *links* to the login page. These spot that entry point.
LOGIN_WORD_RE = re.compile(r"\b(log ?in|sign ?in|masuk|login|signin)\b", re.I)
SIGNUP_WORD_RE = re.compile(r"\b(sign ?up|register|daftar|create account)\b", re.I)
LOGIN_PATH_RE = re.compile(r"/(log-?in|sign-?in|masuk|auth|session|account/login)\b", re.I)
OTP_HINTS = re.compile(r"\b(otp|one[- ]time|verification code|kode verifikasi|2fa|authenticator)\b", re.I)
CAPTCHA_HINTS = re.compile(r"\b(captcha|recaptcha|hcaptcha|verify you are human|robot)\b", re.I)

# Providers that must never receive automated password typing from this agent.
SSO_PROVIDER_RE = re.compile(
    r"\b(google|gmail|microsoft|apple|github|discord|facebook|twitter|x\.com|"
    r"metamask|wallet|coinbase|okx|phantom|sso|oauth)\b",
    re.I,
)

OAUTH_HOST_HINTS = (
    "accounts.google.com",
    "google.com/o/oauth",
    "login.microsoftonline.com",
    "login.live.com",
    "appleid.apple.com",
    "github.com/login",
    "discord.com/oauth",
    "facebook.com/login",
    "twitter.com/i/oauth",
    "x.com/i/oauth",
)


@dataclass(slots=True)
class LoginPlan:
    method: str  # none | form | oneclick | manual | unknown
    email_index: int | None = None
    password_index: int | None = None
    submit_index: int | None = None
    provider_index: int | None = None
    provider_name: str = ""
    notes: list[str] = field(default_factory=list)


class LoginDetector(Agent):
    name = "login"

    def __init__(self, bus, state, browser: BrowserAgent, human_gate, llm=None, vault=None) -> None:
        super().__init__(bus, state, llm)
        self.browser = browser
        self.gate = human_gate
        # Credentials go straight into the vault and are referenced by token.
        # This agent never holds plaintext longer than one statement.
        self.vault = vault

    def _stash(self, label: str, value: str) -> str:
        """Vault a credential and return the token to type into the page."""
        if not value:
            return ""
        if self.vault is None:
            return value
        try:
            return self.vault.put(label, value)
        except ValueError:
            return value  # too short to redact safely; still never logged

    def _target_host(self) -> str:
        return urlparse(self.state.target_url).netloc.lower().removeprefix("www.")

    def _on_target_origin(self, url: str | None = None) -> bool:
        host = urlparse(url or self.browser.page.url).netloc.lower().removeprefix("www.")
        origin = self._target_host()
        return bool(host) and (host == origin or host.endswith("." + origin))

    def _looks_like_oauth_url(self, url: str) -> bool:
        lowered = (url or "").lower()
        return any(hint in lowered for hint in OAUTH_HOST_HINTS)

    # ── detection ────────────────────────────────────────────────────────────
    @staticmethod
    def looks_authenticated(snap: Snapshot) -> bool:
        haystack = " ".join(e.label for e in snap.elements) + " " + snap.digest[:800]
        return bool(LOGGED_IN_HINTS.search(haystack)) and not _has_password_field(snap)

    async def _confirm_authenticated(self, snap: Snapshot | None = None) -> bool:
        """Prefer DOM hints; fall back to 'back on target origin without login UI'."""
        snap = snap or await self.browser.refresh()
        if self.looks_authenticated(snap):
            return True
        if not self._on_target_origin():
            return False
        # On the target site with no password field and no obvious login CTA → likely in.
        if _has_password_field(snap):
            return False
        haystack = " ".join(e.label for e in snap.elements) + " " + snap.digest[:800]
        if LOGIN_HINTS.search(haystack) and not LOGGED_IN_HINTS.search(haystack):
            return False
        # Weak positive: session cookies for the target host already exist.
        try:
            cookies = await self.browser.driver.context.cookies()
            origin = self._target_host()
            if any(
                origin in (c.get("domain") or "").lstrip(".")
                or (c.get("domain") or "").lstrip(".").endswith(origin)
                for c in cookies
                if c.get("name")
            ) and LOGGED_IN_HINTS.search(haystack):
                return True
        except Exception:
            pass
        return self.looks_authenticated(snap)

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
                r"continue with ([a-z0-9 .]+)|sign ?in with ([a-z0-9 .]+)|"
                r"(google|github|apple|microsoft|discord|metamask|wallet|facebook)",
                element.label,
                re.I,
            )
            if match:
                provider = next((g for g in match.groups() if g), "").strip()
                return LoginPlan("oneclick", provider_index=element.index, provider_name=provider)

        return LoginPlan("unknown", notes=["no recognisable login affordance"])

    # ── execution ────────────────────────────────────────────────────────────
    @staticmethod
    def find_login_entry(snap: Snapshot) -> int | None:
        """Index of the control most likely to lead to a login page.

        Landing pages usually do not carry the form itself — they link to it.
        Without this, detection gives up on the home page and the whole run
        proceeds anonymously, which hides every reward that needs an account.
        """
        best: tuple[int, int] | None = None
        for element in snap.elements:
            if element.disabled:
                continue
            score = 0
            if element.href and LOGIN_PATH_RE.search(element.href):
                score += 3
            if LOGIN_WORD_RE.search(element.label):
                score += 2
            if SIGNUP_WORD_RE.search(element.label):
                score -= 1  # prefer sign-in over sign-up when both are present
            if score > 0 and (best is None or score > best[0]):
                best = (score, element.index)
        return best[1] if best else None

    async def _follow_login_entry(self, snap: Snapshot) -> bool:
        index = self.find_login_entry(snap)
        if index is None:
            return False
        element = snap.find(index)
        await self.info(
            f"No login form on this page — following {element.label!r} to find one."
        )
        result = await self.browser.execute({"action": "click", "index": index})
        if not result.ok:
            return False
        await self.browser.execute({"action": "wait", "ms": 1500})
        return True

    def _is_sso_provider(self, name: str) -> bool:
        return bool(name and SSO_PROVIDER_RE.search(name))

    async def _enable_oauth_corridor(self) -> None:
        """Let navigation touch known IdP hosts while the login phase is open."""
        guard = getattr(self.browser, "guardrails", None)
        if guard is not None and hasattr(guard, "allow_oauth_hosts"):
            guard.allow_oauth_hosts(True)

    async def _disable_oauth_corridor(self) -> None:
        guard = getattr(self.browser, "guardrails", None)
        if guard is not None and hasattr(guard, "allow_oauth_hosts"):
            guard.allow_oauth_hosts(False)

    async def _manual_login(
        self,
        reason: str,
        *,
        provider: str = "",
        open_provider_index: int | None = None,
    ) -> bool:
        """Hand the browser to the human for Google/SSO/unknown login UIs."""
        await self._enable_oauth_corridor()
        try:
            if open_provider_index is not None:
                await self.info(
                    f"Opening {provider or 'SSO'} — complete the provider login yourself; "
                    "the agent will not type Google/Microsoft passwords."
                )
                # Prefer popup-aware click so OAuth tabs are tracked.
                result = await self.browser.execute(
                    {
                        "action": "click",
                        "index": open_provider_index,
                        "expect_popup": True,
                    }
                )
                if not result.ok:
                    await self.warn(f"Could not open provider button: {result.detail}")
                else:
                    await self.browser.execute({"action": "wait", "ms": 2000})
                    # If a popup appeared, focus it so the human sees the right surface.
                    popup_url = (result.data or {}).get("popup_url")
                    if popup_url:
                        await self.info(f"OAuth popup detected: {popup_url[:120]}")
                        await self.browser.focus_latest_page()

            label = provider.strip() or "the site"
            prompt = (
                f"{reason}\n\n"
                f"In the browser, finish signing in with {label} "
                f"(email, password, 2FA, CAPTCHA, account picker). "
                f"Wait until you are back on the target site and clearly logged in, "
                f"then reply 'done'."
            )
            await self.emit("gate", f"Manual login required ({label}).")
            answer = await self.gate.ask(prompt)
            if not answer:
                await self.fail("Login aborted — manual confirmation was skipped.")
                return False

            # Give the site a moment to settle cookies after the human finishes.
            await self.browser.execute({"action": "wait", "ms": 1500})
            await self.browser.focus_target_page()
            snap = await self.browser.refresh()

            if await self._confirm_authenticated(snap) or await self._wait_until_authenticated():
                self.state.logged_in = True
                self.state.login_method = f"manual:{provider}" if provider else "manual"
                await self.browser.driver.save_session()
                await self.emit("done", "Authenticated via manual login.")
                return True

            # One more chance — human may have finished one step early.
            again = await self.gate.ask(
                "Still cannot confirm login. Finish any remaining approval in the browser, "
                "make sure the target site shows your account, then reply 'done'."
            )
            if not again:
                await self.warn("Could not confirm authentication after manual login.")
                return False
            await self.browser.execute({"action": "wait", "ms": 1500})
            await self.browser.focus_target_page()
            snap = await self.browser.refresh()
            ok = await self._confirm_authenticated(snap) or await self._wait_until_authenticated()
            self.state.logged_in = ok
            if ok:
                self.state.login_method = f"manual:{provider}" if provider else "manual"
                await self.browser.driver.save_session()
                await self.emit("done", "Authenticated via manual login.")
            else:
                await self.warn("Could not confirm authentication after manual login.")
            return ok
        finally:
            await self.browser.focus_target_page()
            await self._disable_oauth_corridor()

    async def _wait_until_authenticated(self, attempts: int = 6, delay_ms: int = 1500) -> bool:
        """Poll briefly after a human finishes SSO — cookies often land late."""
        for _ in range(attempts):
            await self.browser.focus_target_page()
            # Prefer an open tab that is already back on the target origin.
            try:
                await self.browser.focus_target_page()
            except Exception:
                pass
            snap = await self.browser.refresh()
            if await self._confirm_authenticated(snap):
                return True
            # If we are still on an IdP URL, keep waiting for the redirect back.
            if self._looks_like_oauth_url(self.browser.page.url):
                await self.browser.execute({"action": "wait", "ms": delay_ms})
                continue
            await self.browser.execute({"action": "wait", "ms": delay_ms})
        return False

    async def run(self, credentials: dict[str, str] | None = None) -> bool:
        snap = await self.browser.refresh()
        plan = await self.detect(snap)

        # The form may live one click away. Try that before giving up.
        if plan.method == "unknown" and await self._follow_login_entry(snap):
            snap = await self.browser.refresh()
            plan = await self.detect(snap)

        await self.info(f"Login strategy detected: {plan.method}")

        if plan.method == "none":
            self.state.logged_in = True
            self.state.login_method = "session"
            return True

        if plan.method == "form":
            creds = credentials or {}
            if creds.get("email") and creds.get("password"):
                await self.info("Using the credentials stored in .env for this host.")
            email = creds.get("email") or await self.gate.ask("Email / username for login?")
            password = creds.get("password") or await self.gate.ask(
                "Password? (typed straight into the site — never sent to the AI model)",
                secret=True,
            )
            if not email or not password:
                await self.fail("Login aborted — credentials not supplied.")
                return False

            email_token = self._stash("username", email)
            password_token = self._stash("password", password)
            del email, password  # plaintext lives in the vault from here on

            await self.browser.execute(
                {"action": "fill", "index": plan.email_index, "text": email_token}
            )
            await self.browser.execute(
                {"action": "fill", "index": plan.password_index, "text": password_token}
            )
            if plan.submit_index is not None:
                await self.browser.execute({"action": "click", "index": plan.submit_index})
            else:
                await self.browser.execute({"action": "press", "key": "Enter"})
            self.state.login_method = "form"
            return await self._resolve_challenges()

        if plan.method == "oneclick":
            provider = plan.provider_name or "SSO"
            # Always use the human corridor for one-click providers.
            # Google/Microsoft/etc. block automated passwords; wallets need a
            # human signature the agent cannot forge.
            return await self._manual_login(
                f"Detected one-click login via {provider}.",
                provider=provider,
                open_provider_index=plan.provider_index,
            )

        # Unknown UI — do not fail hard; ask the human to finish login.
        return await self._manual_login(
            "No standard login form found — requesting manual login confirmation."
        )

    async def _resolve_challenges(self, max_rounds: int = 4) -> bool:
        """Loop through OTP / CAPTCHA walls, escalating each to the human."""
        await self._enable_oauth_corridor()
        try:
            for _ in range(max_rounds):
                await self.browser.execute({"action": "wait", "ms": 2000})
                await self.browser.focus_latest_page()
                snap = await self.browser.refresh()
                text = snap.digest[:1500] + " ".join(e.label for e in snap.elements)
                current_url = self.browser.page.url

                if await self._confirm_authenticated(snap):
                    self.state.logged_in = True
                    await self.emit("done", "Authenticated.")
                    await self.browser.driver.save_session()
                    return True

                # Landed on an IdP page after form submit (federated login).
                if self._looks_like_oauth_url(current_url):
                    return await self._manual_login(
                        "Login redirected to an identity provider (Google/Microsoft/SSO).",
                        provider="SSO",
                    )

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
                    code = await self.gate.ask(
                        "Enter the OTP / verification code. It will be typed into the browser only.",
                        secret=True,
                    )
                    if not code:
                        return False
                    otp_field = _find_otp_field(snap)
                    if otp_field is None:
                        await self.warn("OTP was provided, but no verification field was detected.")
                        continue
                    await self.browser.execute(
                        {
                            "action": "fill",
                            "index": otp_field.index,
                            "text": self._stash("otp", code),
                        }
                    )
                    await self.browser.execute({"action": "press", "key": "Enter"})
                    continue

                # nothing recognisable changed — assume the flow finished or stalled
                break

            await self.browser.focus_target_page()
            snap = await self.browser.refresh()
            self.state.logged_in = await self._confirm_authenticated(snap)
            if self.state.logged_in:
                await self.browser.driver.save_session()
                await self.emit("done", "Authenticated.")
            else:
                # Last resort: ask human instead of silent failure (covers odd SSO).
                return await self._manual_login(
                    "Could not confirm authentication automatically."
                )
            return self.state.logged_in
        finally:
            await self.browser.focus_target_page()
            await self._disable_oauth_corridor()


def _find_otp_field(snap: Snapshot):
    """Choose the verification input instead of accidentally refilling email."""
    candidates = []
    for element in snap.elements:
        if element.tag != "input" or element.disabled:
            continue
        label = f"{element.label} {element.name} {element.id}".lower()
        if OTP_HINTS.search(label):
            candidates.append((4, element))
        elif element.type in {"tel", "number"}:
            candidates.append((3, element))
        elif element.type in {"text", ""}:
            candidates.append((1, element))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _has_password_field(snap: Snapshot) -> bool:
    return any(e.type == "password" for e in snap.elements)
