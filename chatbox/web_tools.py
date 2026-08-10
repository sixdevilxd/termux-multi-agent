"""Lightweight free web search via DuckDuckGo HTML (no API key)."""
from __future__ import annotations

import html as html_lib
import re
import urllib.parse
import urllib.request

UA = {"User-Agent": "termux-multi-agent-chatbox/1.0"}


def web_search(query: str, limit: int = 5) -> str:
    q = (query or "").strip()
    if not q:
        return "Kasih kata kunci pencarian."
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q})
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        return f"Web search gagal: {exc}"

    # result blocks
    results = []
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
        body,
        re.I | re.S,
    ):
        href, title, snip = m.group(1), m.group(2), m.group(3)
        # DDG sometimes wraps redirects
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        title = re.sub(r"<[^>]+>", "", title)
        snip = re.sub(r"<[^>]+>", "", snip)
        title = html_lib.unescape(title).strip()
        snip = html_lib.unescape(snip).strip()
        href = html_lib.unescape(href).strip()
        if title and href:
            results.append((title, href, snip))
        if len(results) >= limit:
            break

    if not results:
        # simpler fallback pattern
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.I | re.S):
            href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            if "uddg=" in href:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = qs.get("uddg", [href])[0]
            title = html_lib.unescape(title).strip()
            href = html_lib.unescape(href).strip()
            results.append((title, href, ""))
            if len(results) >= limit:
                break

    if not results:
        return f"Tidak ada hasil untuk `{q}`."

    lines = [f"*Web search:* `{q}`", ""]
    for i, (title, href, snip) in enumerate(results, 1):
        lines.append(f"{i}. *{title}*")
        if snip:
            lines.append(f"   {snip[:180]}")
        lines.append(f"   {href}")
    return "\n".join(lines)
