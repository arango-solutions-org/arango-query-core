"""Language-agnostic NL→query generation loop.

Composes the three shared pieces — an :class:`~arango_query_core.nl.providers.LLMProvider`,
a :class:`~arango_query_core.nl.fewshot.FewShotIndex`, and a
:class:`~arango_query_core.nl.seams.QueryLanguageAdapter` — into the
generate → validate → repair retry loop both ``nl2cypher`` and
``nl2sparql`` need. The engine owns *flow* and *accounting*; every
judgement about the target language lives behind the adapter seams.

This is deliberately the minimal faithful loop. ``nl2cypher``'s full
pipeline carries extra stages (entity resolution, tenant prompt
sections, Anthropic cache splitting) that migrate here incrementally
as the re-point lands, gated by that repo's eval suite — the engine's
API is designed so those arrive as optional collaborators, not
signature breaks.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .host import HostExtensions, host_prompt_section
from .postconditions import (
    Postcondition,
    PostconditionContext,
    PostconditionViolation,
    postcondition_prompt_sections,
    run_postconditions,
)
from .providers import LLMProvider
from .seams import GuardrailVerdict, QueryLanguageAdapter, ValidationResult

_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")

logger = logging.getLogger(__name__)


@dataclass
class NLResult:
    """Outcome of one NL→query generation.

    ``query`` is empty when generation failed (``ok`` False); ``error``
    then carries the last validation failure, postcondition violation or
    guardrail refusal. ``ok`` True with a non-None ``postcondition`` means a
    soft fallback: the retry budget ran out and ``query`` is the first valid
    candidate whose only objection was soft (``PostconditionViolation.soft``).
    Token fields accumulate across retries so cost accounting sees the
    whole conversation, not the last attempt.
    """

    query: str = ""
    ok: bool = False
    error: str = ""
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    guardrail: GuardrailVerdict | None = None
    validation: ValidationResult | None = None
    postcondition: PostconditionViolation | None = None
    provider_errors: int = 0
    """Provider calls that raised and were retried (``retry_provider_errors``)."""


@dataclass
class NLQueryEngine:
    """Generate a target-language query from a natural-language question.

    ``few_shot_k`` examples are retrieved per question from the
    adapter's corpus (seam 2) and rendered under ``## Examples`` — the
    section :func:`~arango_query_core.nl.providers.split_system_for_anthropic_cache`
    treats as the cache boundary, so the static prefix (grammar +
    schema) stays cacheable across questions. ``grounding_k`` entities
    are likewise retrieved per question from the adapter's grounding
    index (seam 6) and rendered after the few-shot section — also
    post-cache-boundary, per-question dynamic content. ``predicate_k``
    predicates are retrieved per question from the adapter's predicate
    index (seam 7) and rendered AFTER the entity section — same
    post-cache-boundary, per-question dynamic content discipline.
    ``path_k`` navigation hints are retrieved per question from the
    adapter's path index (seam 8) and rendered AFTER the predicate
    section — same post-cache-boundary discipline; the adapter resolves
    the anchor/target identifiers from its own seam-6/7 state (D-02).

    ``host_extensions`` (:mod:`~arango_query_core.nl.host`) is rendered straight
    after the grammar section, before ``## Examples``, so it is part of the
    cacheable prefix. Caller postconditions' prompt sections come last; when a
    ``## Examples`` section is present they therefore fall after the cache split.
    """

    provider: LLMProvider
    adapter: QueryLanguageAdapter
    few_shot_k: int = 3
    grounding_k: int = 20
    predicate_k: int = 20
    path_k: int = 5
    max_retries: int = 2
    guardrail_context: dict[str, Any] = field(default_factory=dict)
    postconditions: Sequence[Postcondition] = ()
    postcondition_mapping: Any = None
    retry_provider_errors: bool = False
    """When True, a provider call that raises (a timeout, a 5xx) uses one
    attempt of the ``max_retries`` budget and the loop goes on; the count is
    on :attr:`NLResult.provider_errors`. If no attempt ever returned a
    response, the last exception is re-raised. False (the default) lets the
    first exception propagate, as before."""
    host_extensions: HostExtensions | None = None
    """What the host's engine accepts beyond the target language (:mod:`.host`),
    rendered straight after the grammar section, inside the cacheable prefix.
    ``None`` or a declaration that renders nothing leaves the system prompt
    byte-identical."""

    def generate(self, question: str, *, schema_context: str = "", user_hint: str = "") -> NLResult:
        """Generate a query for *question*.

        *user_hint* is appended to the **first** attempt's user message only —
        e.g. "your previous query failed with ..." when a caller re-asks after a
        failure. It leaves the system prompt (the cacheable prefix), few-shot
        retrieval and postcondition context untouched; retries carry their own
        "previous query was rejected" text.
        """
        result = NLResult()
        system = self._system_prompt(question, schema_context)
        user = f"{question}\n\n{user_hint}" if user_hint else question
        last_provider_error: Exception | None = None
        failure: ValidationResult | None = None
        violation: PostconditionViolation | None = None
        candidate = ""
        # First valid candidate whose only objection was soft: what the call
        # returns if the budget runs out without a clean candidate.
        fallback: tuple[str, ValidationResult, PostconditionViolation] | None = None

        for attempt in range(self.max_retries + 1):
            if attempt > 0 and failure is not None:
                # A postcondition rejection carries its own correction text;
                # otherwise fall back to the adapter's syntactic repair hint.
                if violation is not None:
                    hint = f"{violation.reason} {violation.suggested_hint}".strip()
                else:
                    assert failure is not None
                    hint = self.adapter.repair_hint(candidate, failure) or failure.error
                user = (
                    f"{question}\n\n"
                    f"Your previous {self.adapter.language} query was rejected:\n"
                    f"```{self.adapter.language}\n{candidate}\n```\n"
                    f"Problem: {hint}\n"
                    f"Return a corrected query only."
                )
            if attempt > 0:
                result.retries = attempt
            try:
                content, usage = self.provider.generate(system, user)
            except Exception as exc:
                if not self.retry_provider_errors:
                    raise
                result.provider_errors += 1
                last_provider_error = exc
                logger.warning(
                    "NL provider call failed (attempt %d/%d): %s: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    type(exc).__name__,
                    exc,
                )
                continue
            for key in _USAGE_KEYS:
                setattr(result, key, getattr(result, key) + int(usage.get(key, 0) or 0))
            candidate = self._normalize(_strip_code_fence(content, self.adapter.language))
            failure = self.adapter.validate(candidate)
            result.validation = failure
            if not failure.ok:
                violation = None
                continue
            # Valid query — enforce caller postconditions through the same
            # retry budget (semantic invariants parse/translate cannot catch).
            violation = run_postconditions(
                candidate,
                self.postconditions,
                PostconditionContext(
                    schema_summary=schema_context,
                    question=question,
                    attempt=attempt,
                    mapping=self.postcondition_mapping,
                ),
            )
            result.postcondition = violation
            if violation is None:
                break
            # Soft only if no check objected as hard (run_postconditions
            # returns a hard violation ahead of any soft one).
            if violation.soft and fallback is None:
                fallback = (candidate, failure, violation)
        else:
            # Budget spent without a clean candidate. If one drew only a soft
            # objection, return it — with the objection on the result — rather
            # than refuse a query that may well be right.
            if fallback is not None:
                candidate, result.validation, result.postcondition = fallback
                failure, violation = result.validation, None

        if failure is None and last_provider_error is not None:
            # Every attempt raised: nothing to report but the failure itself.
            raise last_provider_error
        if failure is None or not failure.ok:
            result.error = failure.error if failure else "provider returned nothing"
            return result
        if violation is not None:
            # Budget spent on a valid-but-semantically-rejected query with no
            # soft-only candidate to fall back on — fail closed rather than
            # return a plausible wrong answer.
            result.error = f"{violation.reason} ({violation.code})"
            return result

        verdict = self.adapter.guardrails(candidate, dict(self.guardrail_context))
        result.guardrail = verdict
        if not verdict.allowed:
            result.error = "; ".join(verdict.reasons) or "guardrail refused the query"
            return result

        result.query = candidate
        result.ok = True
        return result

    def _normalize(self, candidate: str) -> str:
        """The adapter's optional deterministic repair (``normalize`` seam),
        applied before validation; the result is what is validated, quoted in
        retries and returned. A ``normalize`` that raises is logged and the
        candidate is kept — a broken repair must not abort generation."""
        normalize = getattr(self.adapter, "normalize", None)
        if not callable(normalize):
            return candidate
        try:
            normalized = normalize(candidate)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("adapter.normalize raised %s: %s; keeping the candidate", type(exc).__name__, exc)
            return candidate
        return normalized if isinstance(normalized, str) else candidate

    def _system_prompt(self, question: str, schema_context: str) -> str:
        sections = [self.adapter.grammar_prompt_section(schema_context)]
        # Static per engine, so it sits with the grammar, before the ``## Examples``
        # cache boundary. ``""`` when nothing is declared, and the join drops it.
        sections.append(host_prompt_section(self.host_extensions))
        index = self.adapter.few_shot_index()
        if index is not None:
            examples = index.format_prompt_section(
                question, k=self.few_shot_k, language=self.adapter.language
            )
            if examples:
                sections.append(examples)
        grounding = self.adapter.grounding_index()
        if grounding is not None:
            block = self.adapter.grounding_prompt_section(question, grounding, k=self.grounding_k)
            if block:
                sections.append(block)
        predicates = self.adapter.predicate_index()
        if predicates is not None:
            pred_block = self.adapter.predicate_prompt_section(question, predicates, k=self.predicate_k)
            if pred_block:
                sections.append(pred_block)
        paths = self.adapter.path_index()
        if paths is not None:
            path_block = self.adapter.path_prompt_section(question, paths, k=self.path_k)
            if path_block:
                sections.append(path_block)
        # Caller postconditions announce their invariants up front so the model
        # aims to satisfy them, not just get corrected after breaking one. They are
        # static per call but appended last: when an ``## Examples`` section is
        # present, split_system_for_anthropic_cache splits there and these land in
        # the uncached suffix; without one, the whole prompt is a single cached block.
        sections.extend(postcondition_prompt_sections(self.postconditions))
        return "\n\n".join(s for s in sections if s)


_FENCED_BLOCK = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)


def _strip_code_fence(content: str, language: str) -> str:
    """Extract the query from a fenced code block, tolerating bare text.

    LLMs frequently wrap the answer in ```<lang> fences despite the
    output-format contract, and often lead with prose ("Here is the
    query:") or follow with an explanation. The first fenced block wherever
    it appears is the query; a response that only opens a fence yields what
    follows it; a response without a fence is returned as is.
    """
    text = content.strip()
    block = _FENCED_BLOCK.search(text)
    if block is not None:
        return block.group(1).strip()
    if not text.startswith("```"):
        return text
    first_newline = text.find("\n")
    if first_newline == -1:
        return text.strip("`").strip()
    body = text[first_newline + 1 :]
    closing = body.rfind("```")
    if closing != -1:
        body = body[:closing]
    return body.strip()
