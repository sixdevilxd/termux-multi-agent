"""The claude_cli provider, exercised against a fake `claude` binary.

No network, no real CLI, no API key — these run anywhere.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from config.settings import Settings
from core.llm import LLMClient, LLMError


def write_fake_claude(tmp_path, body: str):
    """Create an executable stub that stands in for the claude CLI."""
    path = tmp_path / "claude"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def client_for(monkeypatch, tmp_path, body: str, timeout: int = 30) -> LLMClient:
    fake = write_fake_claude(tmp_path, body)
    cfg = Settings(
        llm_provider="claude_cli",
        llm_model="claude-opus-5",
        llm_api_key="",
        claude_bin=str(fake),
        claude_timeout=timeout,
    )
    monkeypatch.setattr("core.llm.settings", cfg)
    return LLMClient()


def run(client: LLMClient, coro_factory):
    async def scenario():
        try:
            return await coro_factory(client)
        finally:
            await client.aclose()

    return asyncio.run(scenario())


# ── configuration ────────────────────────────────────────────────────────────
def test_claude_cli_needs_no_api_key(tmp_path):
    fake = write_fake_claude(tmp_path, "exit 0\n")
    cfg = Settings(llm_provider="claude_cli", llm_api_key="", claude_bin=str(fake))
    assert cfg.validate() == []
    assert cfg.is_local_provider
    assert cfg.resolved_base_url == ""
    assert cfg.api_root == ""


def test_missing_claude_binary_is_reported():
    cfg = Settings(
        llm_provider="claude_cli", llm_api_key="", claude_bin="/nonexistent/claude"
    )
    assert any("not on PATH" in p or "needs the" in p for p in cfg.validate())


def test_local_provider_skips_base_url_warning(tmp_path):
    fake = write_fake_claude(tmp_path, "exit 0\n")
    cfg = Settings(
        llm_provider="claude_cli",
        llm_api_key="",
        llm_base_url="agentrouter.org",
        claude_bin=str(fake),
    )
    assert cfg.warnings() == []


# ── generation ───────────────────────────────────────────────────────────────
SUCCESS = (
    'cat > /dev/null\n'
    'printf \'{"type":"result","subtype":"success","is_error":false,'
    '"result":"pong"}\'\n'
)


def test_result_field_is_unwrapped(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, SUCCESS)
    assert run(client, lambda c: c.chat("sys", "say pong")) == "pong"


def test_json_helper_works_through_the_cli(monkeypatch, tmp_path):
    # Write the payload as a file rather than fighting shell escaping.
    payload = tmp_path / "out.json"
    payload.write_text(
        json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": '```json\n{"tasks": []}\n```',
            }
        )
    )
    body = f'cat > /dev/null\ncat "{payload}"\n'
    client = client_for(monkeypatch, tmp_path, body)
    assert run(client, lambda c: c.json("sys", "list tasks")) == {"tasks": []}


def test_plain_text_output_is_accepted(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, "cat > /dev/null\nprintf 'just text'\n")
    assert run(client, lambda c: c.chat("sys", "hi")) == "just text"


def test_the_prompt_reaches_the_cli_on_stdin(monkeypatch, tmp_path):
    marker = tmp_path / "stdin.txt"
    body = f'cat > "{marker}"\nprintf \'{{"result":"ok"}}\'\n'
    client = client_for(monkeypatch, tmp_path, body)
    run(client, lambda c: c.chat("system prompt", "USER-PROMPT-MARKER"))
    assert marker.read_text() == "USER-PROMPT-MARKER"


def test_secrets_are_redacted_before_reaching_the_cli(monkeypatch, tmp_path):
    from core.secrets import SecretVault

    marker = tmp_path / "stdin.txt"
    fake = write_fake_claude(tmp_path, f'cat > "{marker}"\nprintf \'{{"result":"ok"}}\'\n')
    cfg = Settings(llm_provider="claude_cli", llm_api_key="", claude_bin=str(fake))
    monkeypatch.setattr("core.llm.settings", cfg)

    vault = SecretVault()
    vault.put("password", "hunter2secret")
    client = LLMClient(vault=vault)
    run(client, lambda c: c.chat("sys", "the password is hunter2secret"))

    written = marker.read_text()
    assert "hunter2secret" not in written
    assert "[REDACTED]" in written


# ── failure modes ────────────────────────────────────────────────────────────
def test_non_zero_exit_is_surfaced(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, "echo 'boom' >&2\nexit 3\n")
    with pytest.raises(LLMError, match="exited with code 3"):
        run(client, lambda c: c.chat("sys", "hi"))


def test_cli_error_payload_is_surfaced(monkeypatch, tmp_path):
    body = 'cat > /dev/null\nprintf \'{"is_error":true,"result":"rate limited"}\'\n'
    client = client_for(monkeypatch, tmp_path, body)
    with pytest.raises(LLMError, match="reported an error"):
        run(client, lambda c: c.chat("sys", "hi"))


def test_empty_stdout_is_surfaced(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, "cat > /dev/null\n")
    with pytest.raises(LLMError, match="nothing on stdout"):
        run(client, lambda c: c.chat("sys", "hi"))


def test_timeout_is_bounded(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, "sleep 30\n", timeout=1)
    with pytest.raises(LLMError, match="did not answer within"):
        run(client, lambda c: c.chat("sys", "hi"))


def test_missing_binary_raises_a_helpful_error(monkeypatch):
    cfg = Settings(llm_provider="claude_cli", llm_api_key="", claude_bin="/no/such/claude")
    monkeypatch.setattr("core.llm.settings", cfg)
    client = LLMClient()
    with pytest.raises(LLMError, match="not on PATH"):
        run(client, lambda c: c.chat("sys", "hi"))


def test_list_models_explains_itself(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, "exit 0\n")
    with pytest.raises(LLMError, match="/model"):
        run(client, lambda c: c.list_models())


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_tools_are_disabled_in_the_invocation(monkeypatch, tmp_path):
    argv = tmp_path / "argv.txt"
    body = f'printf "%s\\n" "$@" > "{argv}"\ncat > /dev/null\nprintf \'{{"result":"ok"}}\'\n'
    client = client_for(monkeypatch, tmp_path, body)
    run(client, lambda c: c.chat("sys", "hi"))

    args = argv.read_text()
    assert "--disallowedTools" in args
    assert "Bash" in args
    assert "--print" in args
    assert "--max-turns" in args
