"""The credential-safety guarantee, exercised against a fake page.

These are the tests that matter most: they prove a password can reach the
browser without ever appearing in the run state, the logs, or anything bound
for the model.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.browser_agent import BrowserAgent
from browser.dom import Element, Snapshot
from core.bus import EventBus
from core.guardrails import Guardrails
from core.secrets import SecretVault
from core.state import RunState

PAGE_PAYLOAD = {
    "url": "https://app.example.com/login",
    "title": "Sign in",
    "elements": [],
    "digest": "Sign in to continue",
}


class FakeLocator:
    def __init__(self) -> None:
        self.filled: str | None = None
        self.clicked = False

    @property
    def first(self) -> "FakeLocator":
        return self

    async def scroll_into_view_if_needed(self, **_kw: object) -> None:
        pass

    async def fill(self, value: str) -> None:
        self.filled = value

    async def click(self, **_kw: object) -> None:
        self.clicked = True


class FakePage:
    url = "https://app.example.com/login"

    def __init__(self) -> None:
        self.loc = FakeLocator()

    def locator(self, _selector: str) -> FakeLocator:
        return self.loc

    async def evaluate(self, _js: str) -> dict:
        return PAGE_PAYLOAD

    async def wait_for_load_state(self, *_a: object, **_k: object) -> None:
        pass


class FakeDriver:
    def __init__(self) -> None:
        self.page = FakePage()

    async def save_session(self) -> None:
        pass

    async def screenshot(self, _name: str) -> None:
        return None


def build_agent(elements: list[Element]) -> tuple[BrowserAgent, SecretVault, RunState]:
    state = RunState(target_url="https://app.example.com")
    vault = SecretVault()
    agent = BrowserAgent(
        bus=EventBus(),
        state=state,
        driver=FakeDriver(),
        guardrails=Guardrails("https://app.example.com"),
        llm=None,
        vault=vault,
    )
    agent._snapshot = Snapshot(
        url="https://app.example.com/login",
        title="Sign in",
        elements=elements,
        digest="Sign in to continue",
    )
    return agent, vault, state


PASSWORD_FIELD = Element(
    index=0, tag="input", type="password", role="", label="Password",
    href="", id="pw", name="password", disabled=False, sensitive=True,
)
EMAIL_FIELD = Element(
    index=1, tag="input", type="email", role="", label="Email",
    href="", id="em", name="email", disabled=False, sensitive=False,
)


def test_password_reaches_the_page_but_never_the_run_state():
    async def scenario():
        agent, vault, state = build_agent([PASSWORD_FIELD])
        token = vault.put("password", "hunter2secret")
        result = await agent.execute({"action": "fill", "index": 0, "text": token})
        return agent.driver.page.loc.filled, result, state

    typed, result, state = asyncio.run(scenario())

    assert typed == "hunter2secret"          # the browser got the real value
    assert result.ok
    assert "hunter2secret" not in result.detail
    assert "<secret>" in result.detail
    assert "hunter2secret" not in str(state.to_dict())


def test_plaintext_into_a_credential_field_is_refused():
    """If a planner ever invents a password, we must not type it."""

    async def scenario():
        agent, _vault, _state = build_agent([PASSWORD_FIELD])
        result = await agent.execute(
            {"action": "fill", "index": 0, "text": "guessed-password"}
        )
        return agent.driver.page.loc.filled, result

    typed, result = asyncio.run(scenario())

    assert typed is None          # nothing was ever typed
    assert not result.ok
    assert result.blocked
    assert "credential field" in result.detail


def test_ordinary_fields_still_accept_plaintext():
    async def scenario():
        agent, _vault, _state = build_agent([PASSWORD_FIELD, EMAIL_FIELD])
        result = await agent.execute(
            {"action": "fill", "index": 1, "text": "someone@example.com"}
        )
        return agent.driver.page.loc.filled, result

    typed, result = asyncio.run(scenario())
    assert typed == "someone@example.com"
    assert result.ok


def test_sensitive_elements_are_flagged_to_the_planner():
    rendered = PASSWORD_FIELD.render()
    assert "secret field" in rendered
    assert "vault token" in rendered


@pytest.mark.parametrize("label", ["Delete account", "Withdraw funds"])
def test_guardrails_still_apply_to_fills(label):
    dangerous = Element(
        index=0, tag="input", type="text", role="", label=label,
        href="", id="x", name="x", disabled=False, sensitive=False,
    )

    async def scenario():
        agent, _vault, _state = build_agent([dangerous])
        return await agent.execute({"action": "fill", "index": 0, "text": "anything"})

    result = asyncio.run(scenario())
    assert result.blocked
