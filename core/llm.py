"""Provider-agnostic LLM client.

Supports OpenAI-compatible endpoints (OpenAI, OpenRouter, most local servers),
Anthropic and Gemini. Everything goes through httpx so nothing heavy has to be
compiled on Termux.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from config.settings import settings
from core.logger import get_logger

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
    def __init__(self, timeout: float = 120.0) -> None:
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self.base_url = settings.resolved_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(self, system: str, user: str, temperature: float = 0.1) -> str:
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

    # ── providers ────────────────────────────────────────────────────────────
    async def _openai_compatible(self, system: str, user: str, temperature: float) -> str:
        r = await self._client.post(
            f"{self.base_url}/chat/completions",
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
        return r.json()["choices"][0]["message"]["content"]

    async def _anthropic(self, system: str, user: str, temperature: float) -> str:
        r = await self._client.post(
            f"{self.base_url}/messages",
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
        return "".join(b.get("text", "") for b in r.json().get("content", []))

    async def _gemini(self, system: str, user: str, temperature: float) -> str:
        r = await self._client.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            params={"key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temperature},
            },
        )
        self._raise_for_status(r)
        cands = r.json().get("candidates", [])
        if not cands:
            raise LLMError("Gemini returned no candidates.")
        return "".join(p.get("text", "") for p in cands[0]["content"]["parts"])

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code} from LLM provider: {r.text[:400]}")
