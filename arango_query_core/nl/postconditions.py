"""Caller-supplied semantic postconditions for the NL→query retry loop.

The engine already runs two kinds of check through its generate → validate →
retry loop: the adapter's ``validate`` seam (parse + translate — is the query
*well formed*?) and its ``guardrails`` seam (deployment policy — is it
*allowed*?). Both catch a query that is broken or forbidden.

Postconditions catch a third, more dangerous class: a query that parses,
translates, and returns *plausible* results while being **semantically wrong**
— aggregating over the wrong rows, resolving to the wrong collection, dropping
a required filter. No syntactic check sees it and no human sees it in a demo.

A postcondition is a caller-supplied invariant. It rides the *same* retry
budget as parse and translate: the model is told the rule up front (via
:meth:`Postcondition.prompt_section`, rendered once at the end of the system
prompt), and a violation is fed back into the next prompt exactly like a
validation failure — so the loop gets a chance to *correct* the query rather
than just reject it, and fails closed when the budget is spent — unless a
candidate drew only a *soft* violation (``PostconditionViolation.soft``), in
which case the first such candidate is returned instead.

Semantics (mirrors ``nl2cypher``'s original, language-agnostic here):

* Postconditions run **after** the adapter's ``validate`` succeeds — a retry is
  only ever spent on an already-valid query.
* The **first hard** violation wins; checks are not accumulated. One clear,
  specific correction beats a pile of them. A *soft* violation does not stop
  the run: the remaining checks still see the query, and any hard violation
  among them takes precedence. Only a query no check objects to as hard can
  become a soft fallback, whatever order the checks are listed in.
* A check that **raises** is treated as a violation, not an error — a broken
  postcondition must not turn a safety mechanism into an outage. Fail closed.
* :meth:`Postcondition.prompt_section` renders **once**; it must not vary
  between attempts or provider-side prompt caching is defeated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "PostconditionViolation",
    "PostconditionContext",
    "Postcondition",
    "run_postconditions",
    "postcondition_prompt_sections",
]


@dataclass(frozen=True)
class PostconditionViolation:
    """Why a generated query failed a caller invariant.

    Structurally compatible with any other guardrail-style rejection the loop
    understands: ``reason``, ``suggested_hint`` and ``code`` are all it reads,
    so a violation from an unrelated check can be surfaced through the same
    path without inheritance.
    """

    reason: str
    """What is wrong, in one sentence. Fed into the retry prompt first."""
    suggested_hint: str
    """How to fix it. Also fed into the retry prompt, after ``reason``."""
    code: str = "postcondition_violation"
    """Stable identifier for telemetry / assertions, e.g. ``"time_windowed"``."""
    soft: bool = False
    """A soft violation asks for a retry but does not make the query unusable.

    Use it for an objection that is sometimes the right answer — e.g. "the query
    returned no rows" when the honest answer may be "none". When the retry
    budget is spent, the engine returns the first valid candidate whose only
    objection was soft (with this violation on ``NLResult.postcondition``)
    instead of failing closed. Hard violations (the default) still fail closed.

    Only that first soft candidate is offered to the guardrails: if they refuse
    it, the call fails closed with the guardrail's reason, even when a later
    soft candidate would have passed — the conservative reading.
    """


@dataclass(frozen=True)
class PostconditionContext:
    """What a check is given besides the query itself.

    Passed as a single object so a future field does not change every
    implementer's signature. ``mapping`` is intentionally ``Any`` — the shared
    engine does not depend on any one repo's schema-bundle type; a consumer
    passes whatever its checks need (e.g. a ``MappingBundle``).
    """

    schema_summary: str
    question: str
    attempt: int
    mapping: Any = None


@runtime_checkable
class Postcondition(Protocol):
    """A caller-supplied check on a generated query."""

    code: str
    """Stable identifier, e.g. ``"analog_conditional"``."""

    def check(
        self,
        query: str,
        *,
        context: PostconditionContext,
    ) -> PostconditionViolation | None:
        """Return ``None`` to accept, or a violation to trigger a retry."""
        ...

    def prompt_section(self) -> str:
        """Text injected into the system prompt so the model is told the rule
        up front rather than only corrected after breaking it.

        Return ``""`` to contribute nothing. Rendered once per call — must not
        vary between attempts, or provider-side prompt caching is defeated.
        """
        ...


def run_postconditions(
    query: str,
    postconditions: Sequence[Postcondition] | None,
    context: PostconditionContext,
) -> PostconditionViolation | None:
    """Run *postconditions* in order, returning the first hard violation, or
    else the first soft one.

    A soft violation does not stop the run, so a hard check listed after it
    still sees the query; otherwise a query breaking a hard rule could be
    returned as a soft fallback.

    A check that raises is treated as a (hard) violation rather than being
    allowed to abort the translation: a broken check must not be able to turn a
    safety mechanism into an outage, and failing closed is the conservative
    reading.
    """
    if not postconditions:
        return None

    first_soft: PostconditionViolation | None = None
    for pc in postconditions:
        code = getattr(pc, "code", pc.__class__.__name__)
        try:
            violation = pc.check(query, context=context)
        except Exception as exc:  # noqa: BLE001 - see docstring; fail closed
            return PostconditionViolation(
                code=f"{code}_error",
                reason=f"Postcondition {code!r} raised {type(exc).__name__}: {exc}",
                suggested_hint="Simplify the query; the check could not evaluate it.",
            )
        if violation is None:
            continue
        if not violation.soft:
            return violation
        if first_soft is None:
            first_soft = violation
    return first_soft


def postcondition_prompt_sections(
    postconditions: Sequence[Postcondition] | None,
) -> list[str]:
    """Collect non-empty ``prompt_section()`` output, in caller order.

    Rendered once, at the end of the system prompt, by the engine. When the
    prompt has an ``## Examples`` section the sections fall after
    :func:`~arango_query_core.nl.providers.split_system_for_anthropic_cache`'s
    split, in the uncached suffix. A check that raises here (a broken
    ``prompt_section``) is skipped rather than aborting the whole generation.
    """
    if not postconditions:
        return []
    sections: list[str] = []
    for pc in postconditions:
        try:
            section = pc.prompt_section()
        except Exception:  # noqa: BLE001 - a broken section must not abort generation
            continue
        if section and section.strip():
            sections.append(section.strip())
    return sections
