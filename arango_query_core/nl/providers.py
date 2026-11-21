"""Pluggable LLM provider interface for NL→query pipelines (Cypher, SPARQL, AQL).

Keeps network-facing concerns (HTTP, auth, model selection) isolated from
the prompt-construction and schema-analysis code in ``_core`` and
``_aql``.  Per PRD §1.2, providers receive pre-rendered ``system`` and
``user`` strings and never touch physical schema details on their own.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM backends that generate text from a prompt.

    ``generate`` accepts pre-rendered ``system`` and ``user`` strings and
    returns ``(response_text, usage_dict)`` where *usage_dict* contains
    ``prompt_tokens``, ``completion_tokens``, and ``total_tokens``.  The
    caller is responsible for rendering the system prompt (see
    the adapter's prompt builder — :mod:`arango_query_core.nl.seams`);
    providers no longer format schema context themselves, which
    keeps the §1.2 invariant auditable at a single site and lets future
    waves extend the prompt without touching every provider.
    """

    def generate(
        self,
        system: str,
        user: str,
    ) -> tuple[str, dict[str, int]]:
        """Return ``(content, usage_dict)`` for the given system/user pair."""
        ...


class _BaseChatProvider:
    """Shared HTTP-based chat completion logic for OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        temperature: float = 0.1,
        timeout: int = 30,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self._extra_headers = extra_headers or {}

    def _chat(
        self,
        system: str,
        user: str,
    ) -> tuple[str, dict[str, int]]:
        import requests

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
        details = usage.get("prompt_tokens_details") or {}
        cached = 0
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens", 0) or 0)
        return content, {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "cached_tokens": cached,
        }


class OpenAIProvider(_BaseChatProvider):
    """OpenAI-compatible chat completion provider.

    Reads configuration from constructor args or environment variables:
      - ``api_key`` / ``OPENAI_API_KEY``
      - ``base_url`` / ``OPENAI_BASE_URL``  (default: OpenAI)
      - ``model``   / ``OPENAI_MODEL``      (default: gpt-4o-mini)
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        timeout: int = 30,
    ) -> None:
        super().__init__(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            base_url=base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=temperature,
            timeout=timeout,
        )

    def generate(self, system: str, user: str) -> tuple[str, dict[str, int]]:
        return self._chat(system, user)


class OpenRouterProvider(_BaseChatProvider):
    """OpenRouter-compatible chat completion provider.

    Reads configuration from constructor args or environment variables:
      - ``api_key`` / ``OPENROUTER_API_KEY``
      - ``model``   / ``OPENROUTER_MODEL``   (default: openai/gpt-4o-mini)
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        timeout: int = 30,
    ) -> None:
        super().__init__(
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1",
            model=model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            temperature=temperature,
            timeout=timeout,
        )

    def generate(self, system: str, user: str) -> tuple[str, dict[str, int]]:
        return self._chat(system, user)


_ANTHROPIC_CACHE_BREAKPOINT = "## Examples"
"""Boundary between the cached prefix (prelude + schema) and the
per-request suffix (few-shot examples, resolved entities, user question).

PromptBuilder renders ``## Examples`` as the first *per-question*
section, so everything above that header is static per mapping and a
safe target for Anthropic's ``cache_control: {type: "ephemeral"}``
directive.  If neither WP-25.1 few-shot examples nor WP-25.2 resolved
entities are present, the whole system prompt is static and we mark it
all cached.
"""


def split_system_for_anthropic_cache(system: str) -> list[dict[str, Any]]:
    """Produce Anthropic's `system: [...]` content blocks for prompt caching.

    Splits ``system`` at the first :data:`_ANTHROPIC_CACHE_BREAKPOINT`
    (``## Examples``) into a cached prefix and an uncached suffix.  When
    no breakpoint is present the whole string is a single cached block.

    Returned shape matches Anthropic's Messages API::

        [
          {"type": "text", "text": "<prelude + schema>",
           "cache_control": {"type": "ephemeral"}},
          {"type": "text", "text": "<examples + resolved entities>"},
        ]

    This is exposed as a standalone function so the future
    :class:`AnthropicProvider` and downstream tests can share the exact
    same split logic.
    """
    if not system:
        return [{"type": "text", "text": "", "cache_control": {"type": "ephemeral"}}]
    idx = system.find(_ANTHROPIC_CACHE_BREAKPOINT)
    if idx == -1:
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    prefix = system[:idx].rstrip("\n")
    suffix = system[idx:]
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": prefix,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if suffix:
        blocks.append({"type": "text", "text": suffix})
    return blocks


_ANTHROPIC_API_VERSION = "2023-06-01"
"""Anthropic Messages API version pinned for cache_control support.

``cache_control: {type: "ephemeral"}`` has been GA on this version since
prompt-caching launched; bumping the pin is a deliberate decision and
should be done in a single commit so any field-name renames in the
``usage`` payload are caught by the unit tests below.
"""


class AnthropicProvider:
    """Claude provider with Anthropic-native prompt caching (WP-25.4).

    Hits ``POST /v1/messages`` directly via ``requests`` — same approach
    as :class:`_BaseChatProvider` for OpenAI-compatible endpoints — so
    the ``anthropic`` SDK is **not** a runtime dependency.  Reads
    configuration from constructor args or environment variables:

      - ``api_key``  / ``ANTHROPIC_API_KEY``
      - ``base_url`` / ``ANTHROPIC_BASE_URL`` (default: api.anthropic.com)
      - ``model``    / ``ANTHROPIC_MODEL``    (default: claude-sonnet-4-5)

    The system prompt is split via
    :func:`split_system_for_anthropic_cache` into a cached prefix
    (``prelude + schema``) and an uncached suffix (``examples + resolved
    entities``) before being sent as the Messages API ``system=[...]``
    field.  This is what makes the cache hit on the second of two
    identical requests with the same schema.

    Returned usage dict mirrors OpenAI's shape so the rest of the
    pipeline stays provider-agnostic:

      - ``prompt_tokens``     = input_tokens + cache_read + cache_creation
      - ``completion_tokens`` = output_tokens
      - ``total_tokens``      = prompt + completion
      - ``cached_tokens``     = cache_read_input_tokens

    The OpenAI ``prompt_tokens`` semantics include cached tokens; we
    follow the same convention here so dashboards and the eval gate
    don't need provider-specific arithmetic.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
        ).rstrip("/")
        self.model = model or os.environ.get(
            "ANTHROPIC_MODEL",
            "claude-sonnet-4-5",
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def build_system_blocks(self, system: str) -> list[dict[str, Any]]:
        """Return Anthropic `system=[...]` blocks with cache_control markers."""
        return split_system_for_anthropic_cache(system)

    def generate(
        self,
        system: str,
        user: str,
    ) -> tuple[str, dict[str, int]]:
        """Call the Messages API and return ``(content, usage_dict)``.

        Raises ``requests.HTTPError`` on non-2xx responses so the
        retry loop in :func:`_call_llm_with_retry` can surface the
        message in its retry-context the same way it does for OpenAI
        failures.
        """
        import requests

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.build_system_blocks(system),
            "messages": [{"role": "user", "content": user}],
            "temperature": self.temperature,
        }
        resp = requests.post(
            f"{self.base_url}/messages",
            headers=headers,
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        text = _extract_anthropic_text(data)
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        prompt_tokens = input_tokens + cache_read + cache_creation
        return text, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": prompt_tokens + output_tokens,
            "cached_tokens": cache_read,
        }


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    """Concatenate ``text`` content blocks from a Messages API response.

    Tool-use blocks and other non-text content are skipped; the NL→query
    pipelines only consume textual completions, and the prompt never
    asks Claude to call a tool.
    """
    blocks = data.get("content") or []
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def get_llm_provider() -> LLMProvider | None:
    """Create an LLM provider from environment configuration.

    Resolution order:

    1. ``LLM_PROVIDER=openai``      → :class:`OpenAIProvider`
    2. ``LLM_PROVIDER=openrouter``  → :class:`OpenRouterProvider`
    3. ``LLM_PROVIDER=anthropic``   → :class:`AnthropicProvider`
    4. Auto-detect on key presence, in this priority order:
       ``OPENAI_API_KEY`` > ``OPENROUTER_API_KEY`` > ``ANTHROPIC_API_KEY``.

    OpenAI takes priority on auto-detect because it is the most-tested
    path in WP-25 and the eval gate baseline is calibrated against it;
    Anthropic is opt-in via ``LLM_PROVIDER=anthropic`` until a baseline
    refresh pass calibrates the gate against Claude as well.

    Returns ``None`` when no API key is available for the chosen
    provider (or for any provider, in auto-detect mode).
    """
    explicit = os.environ.get("LLM_PROVIDER", "").lower().strip()
    if explicit == "openrouter":
        openrouter = OpenRouterProvider()
        return openrouter if openrouter.api_key else None
    if explicit == "openai":
        openai_provider = OpenAIProvider()
        return openai_provider if openai_provider.api_key else None
    if explicit == "anthropic":
        anthropic_provider = AnthropicProvider()
        return anthropic_provider if anthropic_provider.api_key else None

    has_openai = bool(os.environ.get("OPENAI_API_KEY", ""))
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY", ""))
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
    if has_openai:
        return OpenAIProvider()
    if has_openrouter:
        return OpenRouterProvider()
    if has_anthropic:
        return AnthropicProvider()
    return None
