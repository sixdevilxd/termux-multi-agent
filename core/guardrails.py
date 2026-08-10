"""Guardrails — the layer that stops an autonomous agent from doing damage.

Three independent checks, all applied before *every* browser action:
  1. domain lock   — never leave the target origin (except allow-listed IdPs during login)
  2. intent filter — never touch destructive / financial controls
  3. budget        — hard cap on total actions per run
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from config.settings import settings

# Anything matching these on a button/link label is refused outright.
DESTRUCTIVE_PATTERNS = [
    r"\bdelete\b", r"\bremove\b", r"\bhapus\b", r"\bdestroy\b",
    r"\bdeactivate\b", r"\bclose account\b", r"\bterminate\b",
    r"\bunsubscribe\b", r"\bcancel (subscription|plan|account)\b",
    r"\breset\b", r"\brevoke\b", r"\bwipe\b",
]

FINANCIAL_PATTERNS = [
    r"\bwithdraw\b", r"\btransfer\b", r"\bsend (money|funds|token)\b",
    r"\bbuy\b", r"\bpurchase\b", r"\bcheckout\b", r"\bpay\b", r"\bbayar\b",
    r"\bupgrade\b", r"\bsubscribe\b", r"\btarik dana\b", r"\bswap\b",
]

SOCIAL_PATTERNS = [
    r"\bpost\b", r"\btweet\b", r"\bpublish\b", r"\bshare\b", r"\binvite\b",
    r"\bsend message\b", r"\bkirim\b",
]

# Identity-provider hosts the agent may touch *only* while login OAuth corridor is open.
# Keep this tight — it is a deliberate hole in the domain lock.
DEFAULT_OAUTH_HOSTS = (
    "accounts.google.com",
    "myaccount.google.com",
    "google.com",
    "googleapis.com",
    "gstatic.com",
    "login.microsoftonline.com",
    "login.live.com",
    "microsoftonline.com",
    "live.com",
    "appleid.apple.com",
    "apple.com",
    "github.com",
    "discord.com",
    "discordapp.com",
    "facebook.com",
    "fb.com",
    "twitter.com",
    "x.com",
    "api.twitter.com",
)

_COMPILED = {
    "destructive": [re.compile(p, re.I) for p in DESTRUCTIVE_PATTERNS],
    "financial": [re.compile(p, re.I) for p in FINANCIAL_PATTERNS],
    "social": [re.compile(p, re.I) for p in SOCIAL_PATTERNS],
}


@dataclass(slots=True)
class Verdict:
    allowed: bool
    reason: str = ""
    category: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Verdict(True)


class Guardrails:
    """Stateless policy + a per-run action budget."""

    def __init__(self, target_url: str, block_social: bool = True) -> None:
        self.origin = urlparse(target_url).netloc.lower().removeprefix("www.")
        self.block_social = block_social
        self.actions_used = 0
        self.blocked: list[str] = []
        self._oauth_open = False
        self._oauth_hosts = {h.lower().removeprefix("www.") for h in DEFAULT_OAUTH_HOSTS}

    def allow_oauth_hosts(self, enabled: bool = True) -> None:
        """Open/close the login-time corridor to known identity providers."""
        self._oauth_open = bool(enabled)

    def _host_allowed(self, host: str) -> bool:
        host = (host or "").lower().removeprefix("www.")
        if not host:
            return True
        if host == self.origin or host.endswith("." + self.origin):
            return True
        if not self._oauth_open:
            return False
        for allowed in self._oauth_hosts:
            if host == allowed or host.endswith("." + allowed):
                return True
        return False

    # ── individual checks ────────────────────────────────────────────────────
    def check_url(self, url: str) -> Verdict:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if not host:
            return ALLOW  # relative navigation stays on-origin
        if self._host_allowed(host):
            return ALLOW
        return Verdict(False, f"off-origin navigation to {host}", "domain")

    def check_label(self, label: str) -> Verdict:
        text = (label or "").strip()
        if not text:
            return ALLOW
        for category, patterns in _COMPILED.items():
            if category == "social" and not self.block_social:
                continue
            for pattern in patterns:
                if pattern.search(text):
                    return Verdict(False, f"{category} control: {text!r}", category)
        return ALLOW

    def check_budget(self) -> Verdict:
        if self.actions_used >= settings.max_actions:
            return Verdict(False, f"action budget exhausted ({settings.max_actions})", "budget")
        return ALLOW

    # ── combined entry point ─────────────────────────────────────────────────
    def check_action(self, kind: str, label: str = "", url: str = "") -> Verdict:
        verdict = self.check_budget()
        if not verdict:
            return verdict
        if kind == "goto" and url:
            verdict = self.check_url(url)
            if not verdict:
                self.blocked.append(verdict.reason)
                return verdict
        if kind in {"click", "fill", "press", "select"}:
            verdict = self.check_label(label)
            if not verdict:
                self.blocked.append(verdict.reason)
                return verdict
        return ALLOW

    def consume(self, n: int = 1) -> None:
        self.actions_used += n
