"""Credential isolation.

Design rule: **a secret never enters an LLM prompt, a log line, or the run
state.** Instead of moving plaintext around, the vault hands out opaque tokens.

  gate asks the human  ->  vault.put("password", "hunter2")  ->  "{{secret:s1}}"
  planner / state / logs only ever see  "{{secret:s1}}"
  BrowserAgent resolves the token in the last instruction before locator.fill()

Two layers of defence:
  1. `resolve()` is called only inside the browser/tool layer.
  2. `redact()` is applied to every string leaving for an LLM, so even if a
     secret somehow lands in a DOM digest it is scrubbed before egress.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"\{\{secret:([a-zA-Z0-9_]+)\}\}")
REDACTED = "[REDACTED]"

# Values shorter than this are too generic to redact safely (a 2-char password
# would blank out half the page). Such secrets are refused instead.
MIN_SECRET_LEN = 4


@dataclass
class SecretVault:
    """Holds plaintext credentials for the lifetime of one run, in memory only."""

    _values: dict[str, str] = field(default_factory=dict, repr=False)
    _labels: dict[str, str] = field(default_factory=dict)
    _counter: int = 0

    def put(self, label: str, value: str) -> str:
        """Store a secret and return the token that stands in for it."""
        if not value:
            return ""
        if len(value) < MIN_SECRET_LEN:
            raise ValueError(
                f"Refusing to vault a secret shorter than {MIN_SECRET_LEN} characters — "
                "it cannot be redacted reliably."
            )
        self._counter += 1
        key = f"s{self._counter}"
        self._values[key] = value
        self._labels[key] = label
        return f"{{{{secret:{key}}}}}"

    def is_token(self, text: str) -> bool:
        return bool(TOKEN_RE.fullmatch((text or "").strip()))

    def contains_token(self, text: str) -> bool:
        return bool(TOKEN_RE.search(text or ""))

    def resolve(self, text: str) -> str:
        """Token -> plaintext. Call this ONLY in the browser/tool layer."""
        return TOKEN_RE.sub(lambda m: self._values.get(m.group(1), m.group(0)), text or "")

    def redact(self, text: str) -> str:
        """Plaintext -> [REDACTED]. Call this on everything bound for an LLM."""
        if not text or not self._values:
            return text
        # longest first, so a secret that contains another is handled correctly
        for value in sorted(self._values.values(), key=len, reverse=True):
            if value in text:
                text = text.replace(value, REDACTED)
        return text

    def describe(self, text: str) -> str:
        """Human-safe rendering of a value that may be a token."""
        match = TOKEN_RE.fullmatch((text or "").strip())
        if match:
            return f"<{self._labels.get(match.group(1), 'secret')}>"
        return text

    def clear(self) -> None:
        self._values.clear()
        self._labels.clear()

    def __len__(self) -> int:
        return len(self._values)
