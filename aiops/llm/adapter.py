"""
Provider-agnostic LLM adapter.

Supports Anthropic, OpenAI, and Google Gemini via raw HTTP so the project has
zero SDK dependencies beyond `requests`. Provider is auto-detected based on
which API key is present in the environment.

Design notes
------------
- We only expose `chat_json(system, messages, schema_hint)` — the planner and
  verifier both need a structured object back, not free text. Each provider
  implementation uses its native "respond with JSON" affordance.
- Retries with exponential backoff on 5xx/429. No circuit breaker — this is
  single-tenant.
- We deliberately do *not* use "function calling" / "tool use" from the
  providers. Tool selection is the agent's job and we want that logic in our
  own code (easier to audit, swap providers, and reason about).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, List, Optional

import requests


class LLMError(RuntimeError):
    pass


@dataclass
class LLMMessage:
    role: str   # "user" | "assistant"
    content: str


class LLM:
    """Abstract chat interface returning a parsed JSON object."""

    name: str = "base"

    def chat_json(
        self,
        system: str,
        messages: List[LLMMessage],
        *,
        schema_hint: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> dict:
        raise NotImplementedError

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Pull the first top-level JSON object out of a possibly-noisy string."""
        s = text.strip()
        if s.startswith("```"):
            lines = s.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
        # locate the first '{' and last matching '}'
        start = s.find("{")
        if start == -1:
            raise LLMError(f"LLM output had no JSON object: {text!r}")
        depth = 0
        end = -1
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end == -1:
            raise LLMError(f"Unbalanced JSON in LLM output: {text!r}")
        try:
            return json.loads(s[start:end])
        except json.JSONDecodeError as e:
            raise LLMError(f"Invalid JSON from LLM: {e}\nraw={text!r}")


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #

class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def chat_json(self, system, messages, *, schema_hint=None, max_tokens=2048, temperature=0.2):
        instr = system
        if schema_hint:
            instr += (
                "\n\nYou MUST respond with a single JSON object matching this schema:\n"
                + schema_hint
                + "\nNo prose, no code fences."
            )
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": instr,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = _post_with_retry("https://api.anthropic.com/v1/messages", headers, payload)
        text = "".join(block.get("text", "") for block in data.get("content", []))
        return self._extract_json(text)


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def chat_json(self, system, messages, *, schema_hint=None, max_tokens=2048, temperature=0.2):
        sys_prompt = system
        if schema_hint:
            sys_prompt += (
                "\n\nRespond with a single JSON object matching this schema:\n"
                + schema_hint
            )
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": sys_prompt}]
            + [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        data = _post_with_retry("https://api.openai.com/v1/chat/completions", headers, payload)
        text = data["choices"][0]["message"]["content"]
        return self._extract_json(text)


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #

class GeminiLLM(LLM):
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def chat_json(self, system, messages, *, schema_hint=None, max_tokens=2048, temperature=0.2):
        sys_prompt = system
        if schema_hint:
            sys_prompt += (
                "\n\nRespond with a single JSON object matching this schema:\n"
                + schema_hint
                + "\nNo markdown fences."
            )
        contents = []
        for m in messages:
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": sys_prompt}]},
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        data = _post_with_retry(url, {"content-type": "application/json"}, payload)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected Gemini response shape: {data}") from e
        return self._extract_json(text)


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #

def _post_with_retry(url: str, headers: dict, payload: dict, *, attempts: int = 4) -> dict:
    delay = 1.0
    for i in range(attempts):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as e:
            if i == attempts - 1:
                raise LLMError(f"Network error after {attempts} attempts: {e}") from e
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
            time.sleep(delay)
            delay *= 2
            continue
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:400]}")
    raise LLMError("exhausted retries")


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def build_llm() -> LLM:
    """
    Pick the first provider whose API key is set.

    Priority: ANTHROPIC_API_KEY > OPENAI_API_KEY > GEMINI_API_KEY.
    """
    if key := os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicLLM(key, os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"))
    if key := os.getenv("OPENAI_API_KEY"):
        return OpenAILLM(key, os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    if key := os.getenv("GEMINI_API_KEY"):
        return GeminiLLM(key, os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
    raise LLMError(
        "No LLM API key found. Set one of ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY."
    )
