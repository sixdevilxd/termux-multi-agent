"""Offline smoke tests — no browser, no network, no API key required.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import asyncio

import pytest

from browser.dom import Element, Snapshot
from core.bus import Event, EventBus
from core.guardrails import Guardrails
from core.llm import extract_json
from core.state import PageInfo, RunState, Task
from tgbot.human_gate import HumanGate


# ── guardrails ───────────────────────────────────────────────────────────────
def test_domain_lock():
    g = Guardrails("https://app.example.com/home")
    assert g.check_url("https://app.example.com/quests")
    assert g.check_url("https://api.app.example.com/v1")
    assert not g.check_url("https://evil.test/steal")


@pytest.mark.parametrize(
    "label",
    ["Delete account", "Withdraw funds", "Checkout now", "Hapus data", "Bayar sekarang"],
)
def test_dangerous_labels_blocked(label):
    g = Guardrails("https://app.example.com")
    assert not g.check_label(label)


@pytest.mark.parametrize("label", ["Claim 50 XP", "Start quiz", "Daily check-in", "Next question"])
def test_benign_labels_allowed(label):
    g = Guardrails("https://app.example.com")
    assert g.check_label(label)


def test_budget_exhausts():
    from config.settings import settings

    g = Guardrails("https://app.example.com")
    g.consume(settings.max_actions)
    assert not g.check_budget()


# ── state ────────────────────────────────────────────────────────────────────
def test_state_roundtrip(tmp_path, monkeypatch):
    from config import settings as settings_module

    monkeypatch.setattr(type(settings_module.settings), "runs_dir",
                        property(lambda _self: tmp_path))
    state = RunState(target_url="https://app.example.com")
    state.pages.append(PageInfo(url="https://app.example.com", title="Home"))
    state.tasks.append(Task(id="a1", type="quiz", title="Daily quiz", url="https://x/q"))
    state.save()

    loaded = RunState.load(state.run_id)
    assert loaded.target_url == state.target_url
    assert loaded.tasks[0].title == "Daily quiz"
    assert loaded.pages[0].title == "Home"


def test_task_counts():
    state = RunState(target_url="https://x")
    state.tasks = [
        Task(id="1", type="quiz", title="a", url="u", status="verified"),
        Task(id="2", type="quest", title="b", url="u", status="failed"),
        Task(id="3", type="quiz", title="c", url="u", status="verified"),
    ]
    assert state.counts() == {"verified": 2, "failed": 1}


# ── dom rendering ────────────────────────────────────────────────────────────
def test_snapshot_render_is_compact():
    elements = [
        Element(0, "a", "", "", "Dashboard", "/dash", "", "", False),
        Element(1, "button", "", "", "Claim 50 XP", "", "", "", False),
    ]
    snap = Snapshot("https://x/q", "Quests", elements, "Complete 3 quests today")
    out = snap.render()
    assert "[1] <button> 'Claim 50 XP'" in out
    assert "Complete 3 quests today" in out
    assert snap.find(1).label == "Claim 50 XP"
    assert snap.find(9) is None


# ── llm json recovery ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw",
    [
        '{"tasks": []}',
        '```json\n{"tasks": []}\n```',
        'Sure! Here you go:\n{"tasks": []}\nHope that helps.',
    ],
)
def test_extract_json_survives_chatty_models(raw):
    assert extract_json(raw) == {"tasks": []}


# ── provider configuration ───────────────────────────────────────────────────
def test_agentrouter_default_host():
    from config.settings import Settings

    s = Settings(llm_provider="agentrouter", llm_base_url="", llm_api_key="k")
    assert s.resolved_base_url == "https://agentrouter.org"
    assert s.api_root == "https://agentrouter.org/v1"
    assert s.is_openai_compatible
    assert s.validate() == []


@pytest.mark.parametrize(
    "written",
    [
        "agentrouter.org",
        "agentrouter.org/",
        "https://agentrouter.org",
        "https://agentrouter.org/",
        "https://agentrouter.org/v1",
        "  agentrouter.org  ",
    ],
)
def test_every_way_of_writing_the_host_resolves_the_same(written):
    """The user should be able to type just the domain and be right."""
    from config.settings import Settings

    s = Settings(llm_provider="agentrouter", llm_base_url=written, llm_api_key="k")
    assert s.api_root == "https://agentrouter.org/v1"
    assert s.validate() == []


def test_v1_is_never_doubled():
    from config.settings import Settings

    s = Settings(llm_provider="openrouter", llm_base_url="", llm_api_key="k")
    assert s.api_root == "https://openrouter.ai/api/v1"
    assert s.api_root.count("/v1") == 1


def test_gemini_keeps_its_own_version_segment():
    from config.settings import Settings

    s = Settings(llm_provider="gemini", llm_base_url="", llm_api_key="k")
    assert s.api_root.endswith("/v1beta")
    assert not s.is_openai_compatible


def test_anthropic_wire_can_also_point_at_agentrouter():
    """agentrouter exposes an Anthropic-compatible API on the same host."""
    from config.settings import Settings

    s = Settings(llm_provider="anthropic", llm_base_url="agentrouter.org", llm_api_key="k")
    assert s.api_root == "https://agentrouter.org/v1"  # -> /v1/messages
    assert not s.is_openai_compatible
    assert s.validate() == []


def test_unknown_provider_is_reported():
    from config.settings import Settings

    s = Settings(llm_provider="nope", llm_api_key="k")
    assert any("Unknown LLM_PROVIDER" in p for p in s.validate())


# ── event bus ────────────────────────────────────────────────────────────────
def test_bus_delivers_and_isolates_failures():
    bus = EventBus()
    seen: list[str] = []

    async def good(e: Event) -> None:
        seen.append(e.message)

    async def broken(_e: Event) -> None:
        raise RuntimeError("subscriber exploded")

    bus.subscribe(broken)
    bus.subscribe(good)
    asyncio.run(bus.publish(Event("r1", "test", "info", "hello")))
    assert seen == ["hello"]
    assert len(bus.history("r1")) == 1


# ── human gate ───────────────────────────────────────────────────────────────
def test_gate_resolves_on_reply():
    async def scenario():
        gate = HumanGate(timeout=5)
        sent: list[str] = []
        gate.set_notifier(lambda text: _collect(sent, text))

        task = asyncio.create_task(gate.ask("OTP?"))
        await asyncio.sleep(0.05)
        token = gate.open_question.token
        assert gate.answer(token, "4711")
        return await task

    assert asyncio.run(scenario()) == "4711"


def test_gate_times_out():
    async def scenario():
        gate = HumanGate(timeout=1)
        return await gate.ask("never answered")

    assert asyncio.run(scenario()) is None


async def _collect(bucket: list[str], text: str) -> None:
    bucket.append(text)
