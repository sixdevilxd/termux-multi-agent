"""Compact page representation for the LLM.

Raw HTML is unusable as model input — it blows the context window and buries
the signal. We extract only what an agent can actually *act on*: indexed
interactive elements plus a short text digest.

Security: the value of a sensitive input (password, OTP, PIN, token, CVV) is
never read out of the DOM. Those elements are reported with an empty label and
`sensitive: true` so the planner still knows a field exists without ever
learning what is in it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

# Shared by both passes: what counts as interactive, what counts as visible,
# and how a label is derived without leaking secrets.
_SHARED_JS = r"""
  const SEL = 'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="radio"], [onclick], [tabindex]:not([tabindex="-1"])';
  const MAX = 120;

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };

  // Anything that could hold a credential or a one-time code.
  const SECRET_RE = /pass|pwd|otp|onetime|one-time|code|token|secret|cvv|cvc|pin|seed|mnemonic|private/i;
  const sensitive = (el) => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'password') return true;
    if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA') return false;
    const meta = (el.getAttribute('name') || '') + ' ' + (el.id || '') + ' ' +
                 (el.getAttribute('autocomplete') || '') + ' ' +
                 (el.getAttribute('aria-label') || '') + ' ' +
                 (el.getAttribute('placeholder') || '');
    return SECRET_RE.test(meta);
  };

  const label = (el) => {
    const secret = sensitive(el);
    const parts = [
      el.getAttribute('aria-label'),
      el.getAttribute('placeholder'),
      el.getAttribute('title'),
      // never surface the contents of a credential field
      secret ? null : el.getAttribute('value'),
      el.getAttribute('name'),
      secret ? null : (el.innerText || '').trim(),
    ].filter(Boolean);
    return (parts[0] || '').replace(/\s+/g, ' ').slice(0, 120);
  };

  const key = (el) => el.tagName + '|' + label(el) + '|' + (el.getAttribute('href') || '');
"""

# Marks every collected element with data-agent-idx so actions can target it by
# index without relying on brittle CSS selectors.
_TAG_JS = "() => {" + _SHARED_JS + r"""
  document.querySelectorAll('[data-agent-idx]').forEach(e => e.removeAttribute('data-agent-idx'));
  let i = 0;
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (i >= MAX) break;
    if (!visible(el)) continue;
    const k = key(el);
    if (seen.has(k)) continue;
    seen.add(k);
    el.setAttribute('data-agent-idx', String(i));
    i++;
  }
  return i;
}
"""

_COLLECT_JS = "() => {" + _SHARED_JS + r"""
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= MAX) break;
    if (!visible(el)) continue;
    const k = key(el);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({
      index: out.length,
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      label: label(el),
      href: el.getAttribute('href') || '',
      id: el.id || '',
      name: el.getAttribute('name') || '',
      disabled: !!el.disabled,
      sensitive: sensitive(el),
    });
  }

  const digest = (document.body ? document.body.innerText : '')
    .replace(/\s+/g, ' ')
    .slice(0, 3000);

  return { url: location.href, title: document.title, elements: out, digest };
}
"""


@dataclass(slots=True)
class Element:
    index: int
    tag: str
    type: str
    role: str
    label: str
    href: str
    id: str
    name: str
    disabled: bool
    sensitive: bool = False

    def render(self) -> str:
        kind = self.type or self.role or self.tag
        extra = f" -> {self.href}" if self.href and not self.href.startswith("javascript") else ""
        flags = ""
        if self.disabled:
            flags += " (disabled)"
        if self.sensitive:
            flags += " (secret field — fill only via a vault token)"
        name = self.label or (self.name or "unnamed")
        return f"[{self.index}] <{kind}> {name!r}{extra}{flags}"


@dataclass(slots=True)
class Snapshot:
    url: str
    title: str
    elements: list[Element]
    digest: str

    def render(self, max_elements: int = 80) -> str:
        """Token-efficient text the LLM actually reads."""
        lines = [f"URL: {self.url}", f"TITLE: {self.title}", "", "INTERACTIVE ELEMENTS:"]
        lines += [e.render() for e in self.elements[:max_elements]]
        if len(self.elements) > max_elements:
            lines.append(f"... {len(self.elements) - max_elements} more elements truncated")
        lines += ["", "PAGE TEXT:", self.digest[:2000]]
        return "\n".join(lines)

    def find(self, index: int) -> Element | None:
        return next((e for e in self.elements if e.index == index), None)

    def links(self) -> list[Element]:
        return [e for e in self.elements if e.href]

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "digest": self.digest,
            "elements": [asdict(e) for e in self.elements],
        }


async def snapshot(page) -> Snapshot:
    """Tag the DOM, then read it back as a Snapshot."""
    await page.evaluate(_TAG_JS)
    raw = await page.evaluate(_COLLECT_JS)
    return Snapshot(
        url=raw["url"],
        title=raw["title"],
        elements=[Element(**e) for e in raw["elements"]],
        digest=raw["digest"],
    )


def selector_for(index: int) -> str:
    return f'[data-agent-idx="{index}"]'
