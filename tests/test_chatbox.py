"""Chatbox routing smoke tests — offline where possible."""
from __future__ import annotations

from chatbox.agent import ChatboxAgent


def test_extract_run_url_plain():
    box = ChatboxAgent()
    assert box.extract_run_url("https://xclass.xiiid.ai/") == "https://xclass.xiiid.ai/"
    assert box.extract_run_url("jalanin https://example.com/app") == "https://example.com/app"
    assert box.extract_run_url("halo apa kabar") is None


def test_tool_route_market():
    box = ChatboxAgent()
    out = box._route_tools("market crypto")
    assert out is not None
    assert "BTC" in out or "Market" in out or "market" in out.lower()


def test_tool_route_wallet_list():
    box = ChatboxAgent()
    out = box._route_tools("list wallet")
    assert out is not None
    assert "wallet" in out.lower() or "Wallet" in out
