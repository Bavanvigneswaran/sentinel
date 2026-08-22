"""The Claude API boundary. Everything above this module (insights_service,
the incidents route, the worker) talks to `AIClient`, never to `anthropic`
directly — the same "one narrow interface" shape app/alerts/notify.py gives
email/push, which is what lets tests substitute a fake client instead of
making real network calls billed to a real API key.

CLAUDE.md's "the LLM explains; it does not detect" is enforced by what this
module does NOT do: no tool use is offered to the model, and a response is
always a single string, stored as inert display text — never parsed as
structured output that could feed back into a decision (an alert threshold,
a rule, anything in analysis/ or the state machine).
"""

from __future__ import annotations

from typing import Protocol

from anthropic import AsyncAnthropic

from app.config import Settings


class AIUnavailable(Exception):
    """Raised when no Anthropic API key is configured — the same
    graceful-absence posture app/alerts/notify.py takes for an unconfigured
    SMTP host or VAPID keypair, surfaced as an exception here because,
    unlike a best-effort notification, a caller needs to know a summary was
    never even attempted rather than silently getting no text."""


class AIClient(Protocol):
    async def summarize(self, *, system: str, prompt: str) -> str: ...

    async def analyze_root_cause(self, *, system: str, prompt: str) -> str: ...


class AnthropicAIClient:
    """Haiku for the short summary, Sonnet for the deeper root-cause
    analysis — two different models because they trade off latency/cost
    against depth differently, matching the roadmap's explicit split."""

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise AIUnavailable("ANTHROPIC_API_KEY is not configured")
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._haiku_model = settings.anthropic_haiku_model
        self._sonnet_model = settings.anthropic_sonnet_model

    async def summarize(self, *, system: str, prompt: str) -> str:
        return await self._complete(
            model=self._haiku_model, system=system, prompt=prompt, max_tokens=200
        )

    async def analyze_root_cause(self, *, system: str, prompt: str) -> str:
        return await self._complete(
            model=self._sonnet_model, system=system, prompt=prompt, max_tokens=600
        )

    async def _complete(self, *, model: str, system: str, prompt: str, max_tokens: int) -> str:
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text").strip()


def build_ai_client(settings: Settings) -> AIClient:
    """Raises AIUnavailable rather than returning None — every caller
    already needs to handle "insights aren't possible right now" as an
    explicit case (a 503 from the route, a skipped tick in the worker), so
    there is no value in a silent-None path that could be forgotten."""
    return AnthropicAIClient(settings)
