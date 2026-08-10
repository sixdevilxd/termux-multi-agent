"""Natural chatbox agent with free tools (crypto, web, wallet, coding help)."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Deque
from urllib.parse import urlparse

from chatbox import crypto_tools, wallet_tools, web_tools
from core.llm import LLMClient
from core.logger import get_logger

log = get_logger("chatbox")

SYSTEM = """You are Bright Scout inside termux-multi-agent — a friendly chatbox on Telegram.

Personality:
- Natural conversation (Indonesian if the user writes Indonesian, else match their language).
- No rigid command menus. Be warm, direct, sharp.
- Skip filler like "Sure!" / "Great question!".
- Lead with the answer. Use short paragraphs, bullets, compact markdown.

You can:
- Chat naturally, brainstorm, explain
- Help write/review/fix code (give clean patches)
- Use TOOL RESULTS the host injects (market, token scan, web search, wallets)
- Explain DLMM / LP concepts
- Help operate the web-runner: tell users they can still /run <url>, /status, /stop

Rules:
- Never invent live prices — trust TOOL RESULTS.
- Never print private keys or seed phrases.
- Crypto answers: brief "not financial advice" when trade-related.
- New tokens = extreme risk (rug/honeypot).
- Keep replies phone-friendly (not huge walls of text unless asked).
- If the user wants a website automation run and pastes a URL clearly as a target, say they can /run it (or that bare URL auto-runs when no gate is open — host may handle).
"""

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
URL_BARE_RE = re.compile(
    r"\b(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?",
    re.I,
)


class ChatboxAgent:
    """Per-bot chat brain with short memory and free tool routing."""

    def __init__(self, max_history: int = 12) -> None:
        self._history: dict[int, Deque[tuple[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_history)
        )
        self._llm: LLMClient | None = None

    def _client(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient(timeout=90.0)
        return self._llm

    async def aclose(self) -> None:
        if self._llm is not None:
            await self._llm.aclose()
            self._llm = None

    def _remember(self, chat_id: int, role: str, text: str) -> None:
        self._history[chat_id].append((role, text[:2000]))

    def _history_block(self, chat_id: int) -> str:
        items = list(self._history.get(chat_id) or [])
        if not items:
            return "(no prior turns)"
        lines = []
        for role, text in items[-8:]:
            lines.append(f"{role.upper()}: {text}")
        return "\n".join(lines)

    def extract_run_url(self, text: str) -> str | None:
        """If the message is essentially just a URL, treat as /run target."""
        raw = (text or "").strip()
        if not raw or raw.startswith("/"):
            return None
        # pure url message
        m = URL_RE.search(raw)
        if m and raw.replace(m.group(0), "").strip() in {"", ".", "!"}:
            return m.group(0)
        # "jalanin https://..." / "run https://..."
        if re.search(r"\b(run|jalanin|jalankan|buka situs|scrape)\b", raw, re.I) and m:
            return m.group(0)
        return None

    def _route_tools(self, text: str) -> str | None:
        t = text.strip()
        low = t.lower()

        # wallets first (may contain secrets)
        m = re.search(
            r"import\s+wallet\s+(solana|sol|ethereum|eth|base|bsc|evm)\s+(?:namanya\s+|name\s+)?([a-zA-Z0-9_-]+)\s*[|:\-]\s*(.+)$",
            t,
            re.I | re.S,
        )
        if m:
            return wallet_tools.import_wallet(m.group(1), m.group(2), m.group(3).strip())

        if re.search(r"\b(list wallet|daftar wallet|wallet saya|my wallets)\b", low):
            return wallet_tools.list_wallets()

        m = re.search(
            r"\b(?:buat|create|generate)\s+wallet\s+(solana|sol|ethereum|eth|base|bsc|evm)"
            r"(?:\s+(?:namanya|name)\s+([a-zA-Z0-9_-]+))?",
            low,
            re.I,
        )
        if m:
            chain = m.group(1)
            name = m.group(2) or "main"
            return wallet_tools.create_wallet(chain, name)

        if re.search(r"\b(scan token|token baru|new launch|trenches|gmgn|hot token|memecoin baru)\b", low):
            chain = "solana"
            if re.search(r"\b(bsc|bnb)\b", low):
                chain = "bsc"
            elif re.search(r"\bbase\b", low):
                chain = "base"
            elif re.search(r"\beth(ereum)?\b", low):
                chain = "ethereum"
            return crypto_tools.new_launches(chain=chain, limit=10)

        if re.search(r"\b(market|harga|price)\b", low) and re.search(
            r"\b(btc|eth|sol|crypto|bitcoin|ethereum|solana|bnb|xrp)\b", low
        ):
            return crypto_tools.market_summary()
        if re.search(r"\b(market crypto|crypto market|ringkasan market|market overview)\b", low):
            return crypto_tools.market_summary()

        if re.search(r"\b(sentimen|sentiment|fear|greed|narrative)\b", low):
            q = ""
            m = re.search(r"(?:tentang|about|for)\s+(.+)$", t, re.I)
            if m:
                q = m.group(1).strip()
            return crypto_tools.sentiment_snapshot(q)

        if re.search(r"\b(dlmm|meteora|liquidity pool|lp range|impermanent loss)\b", low):
            return crypto_tools.explain_dlmm(t)

        # token lookup: "cek token xxx" / contract address
        m = re.search(
            r"\b(?:cek|check|lookup|analisa|analyze)\s+(?:token\s+)?([A-Za-z0-9]{2,})\b",
            t,
            re.I,
        )
        if m and not re.search(r"\b(wallet|bug|kode|code)\b", low):
            cand = m.group(1)
            if cand.lower() not in {"token", "market", "ini", "dong", "ya", "dong"}:
                return crypto_tools.token_lookup(cand)

        if re.fullmatch(r"0x[a-fA-F0-9]{40}", t.strip()) or (
            len(t.strip()) >= 32 and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,48}", t.strip())
        ):
            return crypto_tools.token_lookup(t.strip())

        if re.search(r"\b(cari berita|search web|web search|google|riset|research|cari di web)\b", low):
            q = t
            for prefix in [
                "cari berita",
                "search web",
                "web search",
                "riset",
                "research",
                "cari di web",
                "cari",
            ]:
                if low.startswith(prefix):
                    q = t[len(prefix) :].strip(" :,-")
                    break
            # social-ish queries still go web (X/TikTok/IG connectors not in this bot)
            return web_tools.web_search(q or t)

        if re.search(r"\b(twitter|tiktok|instagram|sosmed|social)\b", low) and re.search(
            r"\b(cari|search|berita|sentiment|sentimen)\b", low
        ):
            return (
                web_tools.web_search(t)
                + "\n\n_Catatan: konektor X/TikTok/IG native belum di-wire di bot ini; "
                "hasil di atas dari web search publik._"
            )

        return None

    async def reply(self, chat_id: int, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "Kirim pesan biasa aja — ngobrol, coding, market, scan token, atau /run <url>."

        # Fast tool path (deterministic, no LLM needed)
        try:
            tool_out = self._route_tools(text)
        except Exception as exc:  # noqa: BLE001
            log.exception("tool route failed")
            tool_out = f"Tool error: {exc}"

        if tool_out is not None and self._tool_only(text):
            self._remember(chat_id, "user", text)
            self._remember(chat_id, "assistant", tool_out)
            return tool_out

        # LLM path with optional tool context
        history = self._history_block(chat_id)
        user_block = (
            f"Chat history:\n{history}\n\n"
            f"User message:\n{text}\n\n"
        )
        if tool_out:
            user_block += (
                "TOOL RESULTS (use these facts; do not invent conflicting numbers):\n"
                f"{tool_out}\n\n"
                "Write a clean final answer for Telegram (markdown-friendly, concise).\n"
            )
        else:
            user_block += (
                "No tool was auto-run. Answer helpfully. "
                "If they need live prices/launches, say they can ask 'market crypto' or 'scan token baru'.\n"
            )

        try:
            answer = await self._client().chat(SYSTEM, user_block, temperature=0.4)
            answer = (answer or "").strip() or tool_out or "..."
        except Exception as exc:  # noqa: BLE001
            log.exception("chatbox llm failed")
            if tool_out:
                answer = tool_out
            else:
                answer = (
                    f"Model error: `{exc}`\n\n"
                    "Sementara kamu masih bisa: /run <url>, bilang *market crypto*, "
                    "*scan token baru*, *buat wallet solana namanya main*."
                )

        self._remember(chat_id, "user", text)
        self._remember(chat_id, "assistant", answer)
        return answer[:3500]

    @staticmethod
    def _tool_only(text: str) -> bool:
        """Short intent messages can skip LLM polish for speed."""
        low = text.lower().strip()
        if len(low) > 80:
            return False
        keys = (
            "market crypto",
            "scan token",
            "token baru",
            "list wallet",
            "daftar wallet",
            "buat wallet",
            "create wallet",
            "sentimen",
            "sentiment",
            "dlmm",
        )
        return any(k in low for k in keys) or bool(
            re.fullmatch(r"0x[a-fA-F0-9]{40}", text.strip())
        )
