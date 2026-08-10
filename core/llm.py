"""Provider-agnostic LLM client.

Supports OpenAI-compatible endpoints (OpenAI, OpenRouter, most local servers),
Anthropic and Gemini. Everything goes through httpx so nothing heavy has to be
compiled on Termux.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from config.settings import settings
from core.logger import get_logger
from core.secrets import SecretVault

log = get_logger("llm")

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Best-effort JSON recovery from a chatty model response."""
    text = text.strip()
    match = _JSON_BLOCK.search(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} or [...] span. Whichever bracket appears
    # first wins — otherwise a nested "[]" inside an object hijacks the match.
    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            candidates.append((start, text[start : end + 1]))

    for _, span in sorted(candidates, key=lambda c: c[0]):
        try:
            return json.loads(span)
        except json.JSONDecodeError:
            continue

    raise LLMError(f"Model did not return valid JSON: {text[:300]}")


class LLMClient:
    def __init__(self, timeout: float = 120.0, vault: "SecretVault | None" = None) -> None:
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        # Host + version segment, derived from LLM_BASE_URL. Writing just
        # `agentrouter.org` in .env is enough; the /v1 is added here.
        self.api_root = settings.api_root
        self.api_key = settings.llm_api_key
        # Every prompt is scrubbed through this before egress. Credentials must
        # never reach a model, even by accident via a DOM digest.
        self.vault = vault
        self._client = httpx.AsyncClient(timeout=timeout)

    def _scrub(self, text: str) -> str:
        return self.vault.redact(text) if self.vault else text

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(self, system: str, user: str, temperature: float = 0.1) -> str:
        system, user = self._scrub(system), self._scrub(user)
        if self.provider == "claude_cli":
            return await self._claude_cli(system, user)
        if not self.api_key:
            raise LLMError("LLM_API_KEY is not set.")
        if self.provider == "anthropic":
            return await self._anthropic(system, user, temperature)
        if self.provider == "gemini":
            return await self._gemini(system, user, temperature)
        return await self._openai_compatible(system, user, temperature)

    async def json(self, system: str, user: str, temperature: float = 0.0) -> Any:
        system = system + "\n\nRespond with raw JSON only. No prose, no code fences."
        return extract_json(await self.chat(system, user, temperature))

    async def list_models(self) -> list[str]:
        """Ask the gateway which model ids it actually accepts.

        Handy for OpenAI-compatible routers where the exact slug
        (e.g. `claude-opus-5`) is only visible once you are authenticated.
        """
        if self.provider == "claude_cli":
            raise LLMError(
                "claude_cli has no model list endpoint. Run `claude` interactively and "
                "use /model to see what your gateway offers, then put that id in "
                "LLM_MODEL (or leave it blank to use the CLI's own default)."
            )
        if not self.api_key:
            raise LLMError("LLM_API_KEY is not set.")
        if self.provider == "gemini":
            r = await self._client.get(f"{self.api_root}/models", params={"key": self.api_key})
            self._raise_for_status(r)
            return sorted(
                m.get("name", "").removeprefix("models/") for m in self._json_body(r).get("models", [])
            )

        headers = (
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
            if self.provider == "anthropic"
            else {"Authorization": f"Bearer {self.api_key}"}
        )
        r = await self._client.get(f"{self.api_root}/models", headers=headers)
        self._raise_for_status(r)
        payload = self._json_body(r)
        rows = payload.get("data", payload if isinstance(payload, list) else [])
        return sorted(str(m.get("id", m)) if isinstance(m, dict) else str(m) for m in rows)

    async def ping(self) -> str:
        """Round-trip one tiny completion to prove the key and model work."""
        return await self.chat(
            "You are a connectivity probe.", "Reply with exactly: pong", temperature=0.0
        )

    # ── providers ────────────────────────────────────────────────────────────
    async def _claude_cli(self, system: str, user: str) -> str:
        """Generate through the Claude Code CLI in headless mode.

        The point of this provider is client identity: some gateways only
        accept a whitelist of known applications and refuse a bespoke HTTP
        client outright. Here the request is genuinely issued by Claude Code,
        using whatever ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN that CLI is
        already configured with — this process never sees the credential.

        Tools are disabled: we want text generation, not an agent that acts.
        """
        cmd = [
            settings.claude_bin,
            "--print",
            "--output-format", "json",
            "--max-turns", "1",
            "--system-prompt", system,
            "--disallowedTools",
            "Bash,Read,Write,Edit,MultiEdit,NotebookEdit,WebFetch,WebSearch,Glob,Grep,Task",
        ]
        if self.model:
            cmd += ["--model", self.model]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise LLMError(
                f"{settings.claude_bin!r} is not on PATH. Install Claude Code with "
                "`npm install -g @anthropic-ai/claude-code`, or set CLAUDE_BIN to its "
                "full path."
            ) from None

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(user.encode()), timeout=settings.claude_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise LLMError(
                f"The claude CLI did not answer within {settings.claude_timeout}s. "
                "Raise CLAUDE_TIMEOUT, or use a faster model."
            ) from None

        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[:400] or "no stderr"
            raise LLMError(f"claude exited with code {proc.returncode}: {detail}")

        raw = stdout.decode(errors="replace").strip()
        if not raw:
            raise LLMError("claude returned nothing on stdout.")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw  # --output-format json not honoured; treat as plain text

        if isinstance(payload, dict):
            if payload.get("is_error") or payload.get("subtype") == "error":
                raise LLMError(f"claude reported an error: {str(payload)[:400]}")
            return str(payload.get("result", raw))
        return raw

    async def _openai_compatible(self, system: str, user: str, temperature: float) -> str:
        r = await self._client.post(
            f"{self.api_root}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        self._raise_for_status(r)
        return self._json_body(r)["choices"][0]["message"]["content"]

    async def _anthropic(self, system: str, user: str, temperature: float) -> str:
        r = await self._client.post(
            f"{self.api_root}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        self._raise_for_status(r)
        return "".join(b.get("text", "") for b in self._json_body(r).get("content", []))

    async def _gemini(self, system: str, user: str, temperature: float) -> str:
        r = await self._client.post(
            f"{self.api_root}/models/{self.model}:generateContent",
            params={"key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temperature},
            },
        )
        self._raise_for_status(r)
        cands = self._json_body(r).get("candidates", [])
        if not cands:
            raise LLMError("Gemini returned no candidates.")
        return "".join(p.get("text", "") for p in cands[0]["content"]["parts"])

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code >= 400:
            body = r.text[:600]
            hint = ""
            if "unauthorized_client" in body:
                hint = (
                    " — the gateway rejected the CLIENT, not your key. Some gateways "
                    "(AgentRouter is one) only accept a whitelist of known apps such as "
                    "Claude Code or Codex, and refuse anything else. Your key may be "
                    "perfectly valid. Switch LLM_PROVIDER to a gateway that serves "
                    "arbitrary API clients — openrouter, gemini, openai or anthropic — "
                    "and clear LLM_BASE_URL so the provider default is used."
                )
            elif r.status_code in (401, 403):
                hint = " — check LLM_API_KEY."
            elif r.status_code == 404:
                hint = (
                    " — this host does not serve that API path. Check LLM_BASE_URL, "
                    "or try a different LLM_PROVIDER wire format against the same host."
                )
            elif r.status_code == 429:
                hint = " — rate limited or out of quota."
            raise LLMError(f"HTTP {r.status_code} from {r.request.url}{hint}\n{body}")

    @staticmethod
    def _json_body(r: httpx.Response) -> Any:
        """Decode a response body, explaining clearly when it is not JSON."""
        try:
            return r.json()
        except ValueError:
            body = r.text.strip()[:200].replace("\n", " ")
            lowered = body.lower()
            if any(tag in lowered for tag in ("waf", "captcha", "cf-browser", "challenge")):
                cause = (
                    "The gateway served a bot-protection challenge instead of the API. "
                    "This usually means the request came from a datacenter/VPN IP — "
                    "retry from a normal mobile or home connection."
                )
            elif "<!doctype html" in lowered or "<html" in lowered:
                cause = (
                    "The gateway returned a web page, not the API. LLM_BASE_URL is "
                    "probably pointing at the website rather than the API host."
                )
            else:
                cause = "The gateway returned a non-JSON body."
            raise LLMError(
                f"{cause}\nURL: {r.request.url}\n"
                f"Content-Type: {r.headers.get('content-type', 'unknown')} "
                f"(HTTP {r.status_code})\nBody starts: {body!r}"
            ) from None
