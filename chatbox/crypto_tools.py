"""Free crypto market helpers (CoinGecko + DexScreener). No paid GMGN key required."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = {"User-Agent": "termux-multi-agent-chatbox/1.0", "Accept": "application/json"}


def _get(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _usd(n: Any) -> str:
    try:
        x = float(n)
    except (TypeError, ValueError):
        return "-"
    if x >= 1_000_000_000:
        return f"${x/1e9:.2f}B"
    if x >= 1_000_000:
        return f"${x/1e6:.2f}M"
    if x >= 1_000:
        return f"${x/1e3:.1f}K"
    if x >= 1:
        return f"${x:,.2f}"
    if x >= 0.0001:
        return f"${x:.6f}"
    return f"${x:.8f}"


def _pct(n: Any) -> str:
    try:
        x = float(n)
    except (TypeError, ValueError):
        return "-"
    return f"{x:+.2f}%"


def market_summary() -> str:
    prices = _get(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum,solana,binancecoin,ripple"
        "&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
    )
    labels = {
        "bitcoin": "BTC",
        "ethereum": "ETH",
        "solana": "SOL",
        "binancecoin": "BNB",
        "ripple": "XRP",
    }
    lines = [
        "*Market snapshot*",
        "",
        "Coin | Price | 24h | MCap",
        "--- | ---: | ---: | ---:",
    ]
    for key, lab in labels.items():
        r = prices.get(key) or {}
        lines.append(
            f"{lab} | {_usd(r.get('usd'))} | {_pct(r.get('usd_24h_change'))} | {_usd(r.get('usd_market_cap'))}"
        )
    try:
        trending = _get("https://api.coingecko.com/api/v3/search/trending")
        names = []
        for item in (trending.get("coins") or [])[:8]:
            c = item.get("item") or {}
            names.append((c.get("symbol") or "?").upper())
        if names:
            lines += ["", "*Trending:* " + ", ".join(names)]
    except Exception:
        pass
    lines += ["", "_Not financial advice._"]
    return "\n".join(lines)


def token_lookup(query: str, limit: int = 6) -> str:
    q = (query or "").strip()
    if not q:
        return "Kasih symbol atau contract address."
    if len(q) >= 32 or q.startswith("0x"):
        data = _get(f"https://api.dexscreener.com/latest/dex/tokens/{urllib.parse.quote(q)}")
        pairs = data.get("pairs") or []
    else:
        data = _get(f"https://api.dexscreener.com/latest/dex/search?q={urllib.parse.quote(q)}")
        pairs = data.get("pairs") or []
    if not pairs:
        return f"Tidak ketemu pair untuk `{q}`."

    def liq(p: dict) -> float:
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    pairs = sorted(pairs, key=liq, reverse=True)[:limit]
    lines = [f"*Hasil untuk* `{q}`", ""]
    for p in pairs:
        base = (p.get("baseToken") or {}).get("symbol") or "?"
        quote = (p.get("quoteToken") or {}).get("symbol") or "?"
        addr = (p.get("baseToken") or {}).get("address") or ""
        chain = p.get("chainId") or "?"
        lines.append(
            f"• *{base}/{quote}* ({chain})  {_usd(p.get('priceUsd'))}  "
            f"{_pct((p.get('priceChange') or {}).get('h24'))}  "
            f"liq {_usd((p.get('liquidity') or {}).get('usd'))}  "
            f"vol {_usd((p.get('volume') or {}).get('h24'))}"
        )
        if p.get("url"):
            lines.append(f"  DexScreener: {p['url']}")
        if addr and chain.lower() in {"solana", "sol"}:
            lines.append(f"  GMGN: https://gmgn.ai/sol/token/{addr}")
        lines.append(f"  `{addr}`")
    lines += ["", "_Not financial advice._"]
    return "\n".join(lines)


def new_launches(chain: str = "solana", limit: int = 10) -> str:
    """Free launch/hot scan via DexScreener boosts + profiles. Links to GMGN pages."""
    boosts = _get("https://api.dexscreener.com/token-boosts/latest/v1") or []
    profiles = _get("https://api.dexscreener.com/token-profiles/latest/v1") or []
    want = None if chain.lower() == "all" else chain.lower()
    rows: list[dict] = []
    seen: set[str] = set()

    def add(item: dict, source: str) -> None:
        ch = (item.get("chainId") or "").lower()
        if want and ch != want:
            return
        addr = item.get("tokenAddress") or ""
        key = f"{ch}:{addr}".lower()
        if not addr or key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "chain": ch,
                "address": addr,
                "source": source,
                "url": item.get("url") or f"https://dexscreener.com/{ch}/{addr}",
                "desc": (item.get("description") or item.get("header") or "").replace("\n", " ")[:90],
            }
        )

    for b in boosts:
        add(b, "boost")
    for p in profiles:
        add(p, "profile")
    rows = rows[:limit]
    if not rows:
        return "Tidak ada token hot/new dari feed gratis DexScreener saat ini."

    lines = [
        f"*Scan token baru / hot* (`{chain}`)",
        "_Gratis via DexScreener. Link GMGN ditambahkan untuk Solana._",
        "",
    ]
    for i, r in enumerate(rows, 1):
        meta = ""
        try:
            data = _get(
                f"https://api.dexscreener.com/latest/dex/tokens/{urllib.parse.quote(r['address'])}"
            )
            pairs = data.get("pairs") or []
            pairs = [p for p in pairs if (p.get("chainId") or "").lower() == r["chain"]] or pairs
            if pairs:
                pairs.sort(
                    key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                    reverse=True,
                )
                p0 = pairs[0]
                sym = (p0.get("baseToken") or {}).get("symbol") or "?"
                meta = (
                    f"{sym}  {_usd(p0.get('priceUsd'))}  "
                    f"{_pct((p0.get('priceChange') or {}).get('h24'))}  "
                    f"liq {_usd((p0.get('liquidity') or {}).get('usd'))}"
                )
        except Exception:
            meta = "?"
        lines.append(f"{i}. `{r['chain']}` {meta}  ({r['source']})")
        if r["desc"]:
            lines.append(f"   _{r['desc']}_")
        lines.append(f"   DS: {r['url']}")
        if r["chain"] in {"solana", "sol"}:
            lines.append(f"   GMGN: https://gmgn.ai/sol/token/{r['address']}")
        lines.append(f"   `{r['address']}`")
    lines += [
        "",
        "_Risiko ekstrem: rug / honeypot / tax. Bukan saran finansial._",
        "_API GMGN trenches butuh key berbayar/terbatas — path gratis = DexScreener + link GMGN._",
    ]
    return "\n".join(lines)


def sentiment_snapshot(query: str = "") -> str:
    prices = _get(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum,solana,binancecoin,dogecoin"
        "&vs_currencies=usd&include_24hr_change=true"
    )
    changes: list[float] = []
    lines = ["*Sentiment snapshot*", ""]
    for key, lab in [
        ("bitcoin", "BTC"),
        ("ethereum", "ETH"),
        ("solana", "SOL"),
        ("binancecoin", "BNB"),
        ("dogecoin", "DOGE"),
    ]:
        r = prices.get(key) or {}
        ch = float(r.get("usd_24h_change") or 0)
        changes.append(ch)
        lines.append(f"• {lab}: {_usd(r.get('usd'))}  {_pct(ch)}")
    avg = sum(changes) / len(changes) if changes else 0.0
    if avg >= 3:
        tilt = "risk-on / bullish tilt"
    elif avg <= -3:
        tilt = "risk-off / bearish tilt"
    else:
        tilt = "mixed / neutral"
    lines += ["", f"*Tape read:* {tilt} (avg majors 24h {avg:+.2f}%)"]
    try:
        boosts = _get("https://api.dexscreener.com/token-boosts/latest/v1") or []
        sol_n = sum(1 for b in boosts if (b.get("chainId") or "") == "solana")
        lines.append(f"*Solana boost heat (sample):* {sol_n} item")
    except Exception:
        pass
    if query.strip():
        try:
            data = _get(
                f"https://api.dexscreener.com/latest/dex/search?q={urllib.parse.quote(query.strip())}"
            )
            pairs = (data.get("pairs") or [])[:4]
            if pairs:
                lines += ["", f"*Dex search* `{query}`:"]
                for p in pairs:
                    base = (p.get("baseToken") or {}).get("symbol")
                    lines.append(
                        f"• {base} ({p.get('chainId')}) {_usd(p.get('priceUsd'))} "
                        f"vol {_usd((p.get('volume') or {}).get('h24'))}"
                    )
        except Exception:
            pass
    lines += ["", "_Price/attention structure only — not financial advice._"]
    return "\n".join(lines)


def explain_dlmm(topic: str = "") -> str:
    return (
        "*DLMM singkat (Meteora-style)*\n\n"
        "DLMM = *Discrete Liquidity Market Maker*: likuiditas ditaruh di *bin* harga "
        "berurutan, bukan satu kurva x*y=k polos.\n\n"
        "• Kamu pilih *range* harga (bin aktif)\n"
        "• Fee + volume di bin itu = yield potensial\n"
        "• Kalau harga keluar range → posisi jadi satu sisi (impermanent loss / idle)\n"
        "• Cocok kalau kamu punya view range; berisiko di chart choppy\n\n"
        "Mau cek pool konkret? Kirim pair address / link DexScreener — nanti saya tarik liq & volume.\n"
        f"{('_Topik: ' + topic) if topic else ''}\n"
        "_Bukan saran finansial. Jangan auto-LP tanpa pahami range._"
    )
