"""Compact page representation for the LLM.

Raw HTML is unusable as model input — it blows the context window and buries
the signal. We extract only what an agent can actually *act on*: indexed
interactive elements plus a short text digest.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

# Collected inside the page. Returns interactive elements in DOM order, each
# tagged with a stable-for-this-snapshot index that actions refer back to.
_COLLECT_JS = r"""
() => {
  const MAX = 120;
  const SEL = 'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="radio"], [onclick], [tabindex]:not([tabindex="-1"])';

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };

  const label = (el) => {
    const parts = [
      el.getAttribute('aria-label'),
      el.getAttribute('placeholder'),
      el.getAttribute('title'),
      el.getAttribute('value'),
      el.getAttribute('name'),
      (el.innerText || '').trim(),
    ].filter(Boolean);
    return (parts[0] || '').replace(/\s+/g, ' ').slice(0, 120);
  };

  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= MAX) break;
    if (!visible(el)) continue;
    const key = el.tagName + '|' + label(el) + '|' + (el.getAttribute('href') || '');
    if (seen.has(key)) continue;
    seen.add(key);
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
    });
  }

  const digest = (document.body ? document.body.innerText : '')
    .replace(/\s+/g, ' ')
    .slice(0, 3000);

  return { url: location.href, title: document.title, elements: out, digest };
}
"""

# Marks every collected element with data-agent-idx so actions can target it by
# index without relying on brittle CSS selectors.
_TAG_JS = r"""
() => {
  const SEL = 'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="radio"], [onclick], [tabindex]:not([tabindex="-1"])';
  const MAX = 120;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };
  const label = (el) => {
    const parts = [
      el.getAttribute('aria-label'), el.getAttribute('placeholder'),
      el.getAttribute('title'), el.getAttribute('value'),
      el.getAttribute('name'), (el.innerText || '').trim(),
    ].filter(Boolean);
    return (parts[0] || '').replace(/\s+/g, ' ').slice(0, 120);
  };
  document.querySelectorAll('[data-agent-idx]').forEach(e => e.removeAttribute('data-agent-idx'));
  let i = 0;
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (i >= MAX) break;
    if (!visible(el)) continue;
    const key = el.tagName + '|' + label(el) + '|' + (el.getAttribute('href') || '');
    if (seen.has(key)) continue;
    seen.add(key);
    el.setAttribute('data-agent-idx', String(i));
    i++;
  }
  return i;
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

    def render(self) -> str:
        kind = self.type or self.role or self.tag
        extra = f" -> {self.href}" if self.href and not self.href.startswith("javascript") else ""
        state = " (disabled)" if self.disabled else ""
        return f"[{self.index}] <{kind}> {self.label!r}{extra}{state}"


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
