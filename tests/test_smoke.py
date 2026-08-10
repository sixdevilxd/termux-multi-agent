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


# ── credential isolation ─────────────────────────────────────────────────────
def test_vault_hands_out_tokens_not_plaintext():
    from core.secrets import SecretVault

    vault = SecretVault()
    token = vault.put("password", "hunter2secret")
    assert "hunter2secret" not in token
    assert vault.is_token(token)
    assert vault.resolve(token) == "hunter2secret"
    assert vault.describe(token) == "<password>"


def test_vault_redacts_secrets_from_outbound_text():
    from core.secrets import SecretVault

    vault = SecretVault()
    vault.put("password", "hunter2secret")
    leaked = "the form now shows hunter2secret in it"
    assert "hunter2secret" not in vault.redact(leaked)
    assert "[REDACTED]" in vault.redact(leaked)


def test_vault_redacts_longest_secret_first():
    from core.secrets import SecretVault

    vault = SecretVault()
    vault.put("short", "abcd1234")
    vault.put("long", "abcd1234efgh")
    assert vault.redact("value=abcd1234efgh") == "value=[REDACTED]"


def test_vault_refuses_unredactable_secret():
    from core.secrets import SecretVault

    with pytest.raises(ValueError):
        SecretVault().put("pin", "12")


def test_vault_clear_removes_everything():
    from core.secrets import SecretVault

    vault = SecretVault()
    vault.put("password", "hunter2secret")
    vault.clear()
    assert len(vault) == 0
    assert vault.redact("hunter2secret") == "hunter2secret"


def test_llm_client_scrubs_prompts_through_the_vault():
    from core.llm import LLMClient
    from core.secrets import SecretVault

    vault = SecretVault()
    vault.put("password", "hunter2secret")
    client = LLMClient(vault=vault)
    assert "hunter2secret" not in client._scrub("page text hunter2secret here")


# ── reward vocabulary ────────────────────────────────────────────────────────
def test_counters_use_the_sites_own_words():
    from core.rewards import extract_counters

    text = "You have 1,240 Sparks and a 7 day Streak"
    counters = extract_counters(text, ["Sparks", "Streak"])
    assert counters["sparks"] == 1240.0
    assert counters["streak"] == 7.0


def test_counters_still_find_generic_units_without_vocabulary():
    from core.rewards import extract_counters

    assert extract_counters("Total: 350 XP", [])["xp"] == 350.0


def test_counters_handle_unit_before_number():
    from core.rewards import extract_counters

    assert extract_counters("Poin: 88", ["Poin"])["poin"] == 88.0


def test_counters_prefer_the_running_total_over_an_increment():
    from core.rewards import extract_counters

    text = "+10 XP earned. Your balance is 1,250 XP."
    assert extract_counters(text, [])["xp"] == 1250.0


def test_counter_diff_reports_only_movement():
    from core.rewards import describe_delta, diff_counters

    delta = diff_counters({"xp": 100.0, "gems": 5.0}, {"xp": 150.0, "gems": 5.0})
    assert delta == {"xp": 50.0}
    assert describe_delta(delta) == "xp +50"


def test_state_exposes_learned_vocabulary_and_delta():
    state = RunState(target_url="https://x")
    state.understanding = {"reward_vocabulary": ["Sparks"]}
    state.reward_baseline = {"sparks": 10.0}
    state.reward_final = {"sparks": 35.0}
    assert state.vocabulary == ["Sparks"]
    assert state.reward_delta() == {"sparks": 25.0}


# ── task ranking ─────────────────────────────────────────────────────────────
def test_tasks_sort_by_priority_then_confidence():
    tasks = [
        Task(id="a", type="quiz", title="low prio", url="u", priority=4, confidence=0.9),
        Task(id="b", type="quiz", title="high prio", url="u", priority=1, confidence=0.5),
        Task(id="c", type="quiz", title="high prio, surer", url="u", priority=1, confidence=0.8),
    ]
    assert [t.id for t in sorted(tasks, key=lambda t: t.rank())] == ["c", "b", "a"]


# ── browser configuration ────────────────────────────────────────────────────
def test_cdp_mode_needs_no_chromium_path():
    from config.settings import Settings

    s = Settings(browser_mode="cdp", chrome_path="", llm_api_key="k")
    assert s.validate() == []


def test_launch_mode_without_chrome_path_is_flagged():
    from config.settings import Settings

    s = Settings(browser_mode="launch", chrome_path="", llm_api_key="k")
    assert any("CHROME_PATH" in p for p in s.validate())


def test_missing_chromium_binary_is_caught_early(tmp_path):
    from config.settings import Settings

    s = Settings(
        browser_mode="launch",
        chrome_path=str(tmp_path / "nope" / "chromium"),
        llm_api_key="k",
    )
    assert any("does not exist" in p for p in s.validate())


def test_existing_chromium_binary_passes(tmp_path):
    from config.settings import Settings

    fake = tmp_path / "chromium"
    fake.write_text("#!/bin/sh\n")
    s = Settings(browser_mode="launch", chrome_path=str(fake), llm_api_key="k")
    assert s.validate() == []


def test_extra_browser_args_are_passed_through():
    from config.settings import Settings

    s = Settings(browser_args=("--single-process", "--no-zygote"), llm_api_key="k")
    assert "--single-process" in s.browser_args


def test_invalid_browser_mode_is_reported():
    from config.settings import Settings

    s = Settings(browser_mode="magic", llm_api_key="k")
    assert any("BROWSER_MODE" in p for p in s.validate())


# ── login entry discovery ────────────────────────────────────────────────────
def _el(index, label, href="", tag="a", disabled=False):
    return Element(
        index=index, tag=tag, type="", role="", label=label,
        href=href, id="", name="", disabled=disabled,
    )


def _snap(elements):
    return Snapshot("https://app.example.com", "Home", elements, "welcome")


def test_login_link_is_found_by_href():
    from agents.login_detector import LoginDetector

    snap = _snap([_el(0, "Home", "/"), _el(1, "Sign in", "/login")])
    assert LoginDetector.find_login_entry(snap) == 1


def test_login_link_is_found_by_indonesian_label():
    from agents.login_detector import LoginDetector

    snap = _snap([_el(0, "Beranda", "/"), _el(1, "Masuk", "/akun")])
    assert LoginDetector.find_login_entry(snap) == 1


def test_sign_in_is_preferred_over_sign_up():
    from agents.login_detector import LoginDetector

    snap = _snap([_el(0, "Sign up", "/register"), _el(1, "Log in", "/login")])
    assert LoginDetector.find_login_entry(snap) == 1


def test_no_login_affordance_returns_none():
    from agents.login_detector import LoginDetector

    snap = _snap([_el(0, "Pricing", "/pricing"), _el(1, "Docs", "/docs")])
    assert LoginDetector.find_login_entry(snap) is None


def test_disabled_login_control_is_ignored():
    from agents.login_detector import LoginDetector

    snap = _snap([_el(0, "Sign in", "/login", disabled=True)])
    assert LoginDetector.find_login_entry(snap) is None


# ── overlay dismissal ────────────────────────────────────────────────────────
def _btn(index, label, disabled=False):
    return Element(
        index=index, tag="button", type="", role="", label=label,
        href="", id="", name="", disabled=disabled,
    )


def _modal_snap(elements, modal=True):
    return Snapshot("https://app.example.com", "Home", elements, "welcome", modal)


def test_single_button_welcome_dialog_is_dismissed():
    """The real case: one button, no links, no inputs, body scroll locked."""
    from agents.overlay import OverlayDismisser

    snap = _modal_snap([_btn(0, "Hello, XIIID")])
    assert OverlayDismisser.pick_dismiss_control(snap) == 0


def test_glyph_close_button_wins():
    from agents.overlay import OverlayDismisser

    snap = _modal_snap([_btn(0, "Subscribe"), _btn(1, "×")])
    assert OverlayDismisser.pick_dismiss_control(snap) == 1


@pytest.mark.parametrize(
    "label", ["Got it", "Lanjutkan", "Accept", "Mengerti", "Let's go", "Skip"]
)
def test_dismiss_words_are_recognised(label):
    from agents.overlay import OverlayDismisser

    snap = _modal_snap([_btn(0, "Something else"), _btn(1, label)])
    assert OverlayDismisser.pick_dismiss_control(snap) == 1


@pytest.mark.parametrize("label", ["Delete account", "Bayar sekarang", "Upgrade", "Sign up"])
def test_dangerous_controls_are_never_pressed(label):
    from agents.overlay import OverlayDismisser

    snap = _modal_snap([_btn(0, label)])
    assert OverlayDismisser.pick_dismiss_control(snap) is None


def test_busy_page_is_not_treated_as_a_dialog():
    """Many buttons means this is the app, not an overlay — do not guess."""
    from agents.overlay import OverlayDismisser

    snap = _modal_snap([_btn(i, f"Item {i}") for i in range(6)])
    assert OverlayDismisser.pick_dismiss_control(snap) is None


def test_disabled_button_is_skipped():
    from agents.overlay import OverlayDismisser

    snap = _modal_snap([_btn(0, "Got it", disabled=True)])
    assert OverlayDismisser.pick_dismiss_control(snap) is None


def test_snapshot_modal_flag_defaults_off():
    assert _snap([]).modal is False


# ── stored credentials ───────────────────────────────────────────────────────
def _creds_settings(**kw):
    from config.settings import Settings

    base = dict(
        llm_provider="agentrouter",
        llm_api_key="k",
        login_email="me@example.com",
        login_password="hunter2secret",
    )
    base.update(kw)
    return Settings(**base)


def test_credentials_are_used_on_the_bound_host():
    s = _creds_settings(login_domain="app.example.com")
    assert s.credentials_for("https://app.example.com/login") == {
        "email": "me@example.com",
        "password": "hunter2secret",
    }


def test_credentials_are_withheld_from_other_hosts():
    s = _creds_settings(login_domain="app.example.com")
    assert s.credentials_for("https://evil.test/login") == {}


def test_credentials_cover_subdomains_of_the_bound_host():
    s = _creds_settings(login_domain="example.com")
    assert s.credentials_for("https://app.example.com/") != {}


def test_www_prefix_does_not_break_the_binding():
    s = _creds_settings(login_domain="www.example.com")
    assert s.credentials_for("https://example.com/") != {}


def test_unbound_credentials_are_used_anywhere_but_warned_about():
    s = _creds_settings(login_domain="")
    assert s.credentials_for("https://anything.test/") != {}
    assert any("LOGIN_DOMAIN is empty" in w for w in s.warnings())


def test_no_credentials_means_no_dict_and_no_warning():
    from config.settings import Settings

    s = Settings(llm_provider="agentrouter", llm_api_key="k")
    assert not s.has_stored_credentials
    assert s.credentials_for("https://app.example.com/") == {}
    assert s.warnings() == []


def test_half_configured_credentials_are_ignored():
    s = _creds_settings(login_password="")
    assert s.credentials_for("https://app.example.com/") == {}


# ── login is mandatory ───────────────────────────────────────────────────────
def test_login_is_required_by_default():
    from config.settings import Settings

    assert Settings(llm_api_key="k").require_login is True


def test_login_requirement_can_be_disabled():
    from config.settings import Settings

    assert Settings(llm_api_key="k", require_login=False).require_login is False


def test_hint_points_at_missing_credentials():
    from agents.orchestrator import login_failure_hint

    hint = login_failure_hint(has_credentials=False, method="form")
    assert "LOGIN_EMAIL" in hint


def test_hint_points_at_wrong_credentials():
    from agents.orchestrator import login_failure_hint

    hint = login_failure_hint(has_credentials=True, method="form")
    assert "could not be confirmed" in hint
    assert "REQUIRE_LOGIN=false" in hint


def test_hint_explains_an_undetectable_login():
    from agents.orchestrator import login_failure_hint

    hint = login_failure_hint(has_credentials=True, method="unknown")
    assert "no login flow" in hint


def test_summary_shouts_when_the_run_stopped_unauthenticated():
    from agents.reporter import Reporter

    state = RunState(target_url="https://app.example.com")
    state.logged_in = False
    state.notes.append("login required but no credentials were available")
    summary = Reporter.telegram_summary(state)
    assert "STOPPED" in summary
    assert "not authenticated" in summary.lower()


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
