"""Caller-supplied postconditions in the engine retry loop.

Covers the semantics the feature promises: postconditions run only on valid
queries, share the retry budget, feed their correction back into the next
prompt, fail closed on exhaustion, first-violation-wins, a raising check is a
violation (not an outage), the rule is announced once in the system prompt,
and the guardrail gate still runs after. Soft violations: retried the same way,
returned as a fallback on exhaustion, never ahead of a hard violation whatever
the check order. All with fakes — no network.
"""

from __future__ import annotations

from typing import Any

from arango_query_core.nl import (
    GuardrailVerdict,
    NLQueryEngine,
    PostconditionContext,
    PostconditionViolation,
    ValidationResult,
    run_postconditions,
    split_system_for_anthropic_cache,
)
from arango_query_core.nl.fewshot import FewShotIndex


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> tuple[str, dict[str, int]]:
        self.calls.append((system, user))
        content = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        return content, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


class FakeAdapter:
    """Valid iff the query contains 'SELECT'."""

    language = "sparql"

    def __init__(self, *, allow: bool = True) -> None:
        self._allow = allow

    def grammar_prompt_section(self, schema_context: str) -> str:
        return "## Grammar\nWrite SPARQL only."

    def few_shot_index(self):
        return None

    def grounding_index(self):
        return None

    def predicate_index(self):
        return None

    def path_index(self):
        return None

    def validate(self, query: str) -> ValidationResult:
        if "SELECT" in query:
            return ValidationResult(ok=True)
        return ValidationResult(ok=False, error="not a SELECT", code="E_PARSE")

    def repair_hint(self, query: str, failure: ValidationResult) -> str:
        return "Emit a SELECT query."

    def guardrails(self, query: str, context: dict[str, Any]) -> GuardrailVerdict:
        if self._allow:
            return GuardrailVerdict(allowed=True)
        return GuardrailVerdict(allowed=False, reasons=["tenant scope violation"])


class NeedsLimit:
    """Requires a LIMIT clause — a stand-in domain invariant."""

    code = "needs_limit"

    def prompt_section(self) -> str:
        return "INVARIANT: every query MUST include a LIMIT clause."

    def check(self, query: str, *, context: PostconditionContext):
        if "LIMIT" in query.upper():
            return None
        return PostconditionViolation(
            code=self.code, reason="Query has no LIMIT.", suggested_hint="Add LIMIT 100."
        )


def _engine(responses, postconditions=(), *, allow=True, max_retries=2):
    return NLQueryEngine(
        provider=FakeProvider(responses),
        adapter=FakeAdapter(allow=allow),
        postconditions=postconditions,
        max_retries=max_retries,
    )


def test_noop_when_no_postconditions():
    eng = _engine(["SELECT * LIMIT 1"])
    r = eng.generate("q")
    assert r.ok and r.retries == 0 and r.postcondition is None


def test_violation_triggers_retry_then_succeeds():
    eng = _engine(["SELECT *", "SELECT * LIMIT 5"], [NeedsLimit()])
    r = eng.generate("q")
    assert r.ok is True
    assert r.retries == 1
    assert r.query == "SELECT * LIMIT 5"
    assert r.postcondition is None


def test_exhaustion_fails_closed():
    eng = _engine(["SELECT *"], [NeedsLimit()], max_retries=2)
    r = eng.generate("q")
    assert r.ok is False
    assert r.retries == 2
    assert r.query == ""  # a valid-but-rejected query is never returned
    assert r.postcondition is not None and r.postcondition.code == "needs_limit"
    assert "no limit" in r.error.lower() and "needs_limit" in r.error


def test_correction_is_fed_into_the_retry_prompt():
    eng = _engine(["SELECT *", "SELECT * LIMIT 5"], [NeedsLimit()])
    eng.generate("q")
    retry_user = eng.provider.calls[1][1]
    assert "Add LIMIT 100." in retry_user  # the suggested_hint reached the model


def test_first_violation_wins():
    class AlwaysA:
        code = "a"

        def prompt_section(self):
            return ""

        def check(self, query, *, context):
            return PostconditionViolation(code="a", reason="A", suggested_hint="fix A")

    class AlwaysB:
        code = "b"

        def prompt_section(self):
            return ""

        def check(self, query, *, context):
            raise AssertionError("second check must not run when the first fails")

    eng = _engine(["SELECT * LIMIT 1"], [AlwaysA(), AlwaysB()], max_retries=0)
    r = eng.generate("q")
    assert r.ok is False and r.postcondition.code == "a"


def test_raising_check_is_a_violation_not_an_outage():
    class Boom:
        code = "boom"

        def prompt_section(self):
            return ""

        def check(self, query, *, context):
            raise ValueError("kaboom")

    eng = _engine(["SELECT * LIMIT 1"], [Boom()], max_retries=1)
    r = eng.generate("q")  # must not raise
    assert r.ok is False
    assert r.postcondition.code == "boom_error"


def test_prompt_section_rendered_once_in_system_prompt():
    eng = _engine(["SELECT *", "SELECT * LIMIT 5"], [NeedsLimit()])
    eng.generate("q")
    system = eng.provider.calls[0][0]
    assert "INVARIANT: every query MUST include a LIMIT clause." in system
    assert system.count("INVARIANT: every query MUST include a LIMIT clause.") == 1
    # system prefix is stable across attempts (cache-friendly)
    assert eng.provider.calls[0][0] == eng.provider.calls[1][0]


class _OneExample:
    def retrieve(self, question, k=3):
        return [("list everything", "SELECT * LIMIT 10")]


class ExamplesAdapter(FakeAdapter):
    def few_shot_index(self):
        return FewShotIndex(_OneExample())


def test_prompt_section_when_examples_present_should_fall_after_the_cache_split():
    eng = NLQueryEngine(provider=FakeProvider([]), adapter=ExamplesAdapter(), postconditions=[NeedsLimit()])
    cached, uncached = split_system_for_anthropic_cache(eng._system_prompt("q", ""))
    assert "INVARIANT" not in cached["text"]
    assert uncached["text"].endswith("INVARIANT: every query MUST include a LIMIT clause.")


def test_prompt_section_when_no_examples_should_stay_in_the_single_cached_block():
    eng = _engine([], [NeedsLimit()])
    (block,) = split_system_for_anthropic_cache(eng._system_prompt("q", ""))
    assert "cache_control" in block and "INVARIANT" in block["text"]


def test_postconditions_only_run_on_valid_queries():
    # First reply is invalid (no SELECT) -> validate fails, postcondition not
    # reached; second reply is valid AND satisfies the postcondition.
    checked: list[str] = []

    class Recorder:
        code = "rec"

        def prompt_section(self):
            return ""

        def check(self, query, *, context):
            checked.append(query)
            return None

    eng = _engine(["nonsense", "SELECT * LIMIT 1"], [Recorder()])
    r = eng.generate("q")
    assert r.ok is True
    assert checked == ["SELECT * LIMIT 1"]  # never ran on the invalid candidate


def test_guardrail_still_gates_after_postconditions():
    eng = _engine(["SELECT * LIMIT 1"], [NeedsLimit()], allow=False)
    r = eng.generate("q")
    assert r.ok is False
    assert "tenant scope" in r.error


def test_run_postconditions_helper_empty_is_none():
    ctx = PostconditionContext(schema_summary="", question="q", attempt=0)
    assert run_postconditions("SELECT 1", None, ctx) is None
    assert run_postconditions("SELECT 1", [], ctx) is None


class NonEmpty:
    """Soft stand-in for "the query returned no rows": a candidate without
    'WHERE' is treated as empty. Sometimes empty is the right answer, so the
    objection is soft."""

    code = "non_empty"

    def prompt_section(self) -> str:
        return ""

    def check(self, query: str, *, context: PostconditionContext):
        if "WHERE" in query:
            return None
        return PostconditionViolation(
            code=self.code,
            reason="The query returned no rows.",
            suggested_hint="Check literals and labels.",
            soft=True,
        )


def test_soft_violation_is_retried_like_a_hard_one():
    eng = _engine(["SELECT *", "SELECT * WHERE {}"], [NonEmpty()])
    r = eng.generate("q")
    assert r.ok is True and r.retries == 1
    assert r.query == "SELECT * WHERE {}"
    assert r.postcondition is None
    assert "Check literals and labels." in eng.provider.calls[1][1]


def test_soft_exhaustion_returns_first_soft_candidate():
    eng = _engine(["SELECT 1", "SELECT 2", "SELECT 3"], [NonEmpty()], max_retries=2)
    r = eng.generate("q")
    assert r.ok is True and r.error == ""
    assert r.retries == 2  # the whole budget was spent first
    assert r.query == "SELECT 1"  # the first candidate, not the last
    assert r.postcondition is not None and r.postcondition.soft
    assert r.postcondition.code == "non_empty"


def test_soft_fallback_survives_a_later_invalid_candidate():
    eng = _engine(["SELECT 1", "nonsense"], [NonEmpty()], max_retries=1)
    r = eng.generate("q")
    assert r.ok is True and r.query == "SELECT 1"
    assert r.validation is not None and r.validation.ok


def test_soft_fallback_survives_a_later_hard_violation():
    # Soft-only first candidate (has LIMIT, no WHERE); the second breaks the
    # hard invariant. The soft candidate is the one worth returning.
    eng = _engine(["SELECT * LIMIT 1", "SELECT *"], [NeedsLimit(), NonEmpty()], max_retries=1)
    r = eng.generate("q")
    assert r.ok is True and r.query == "SELECT * LIMIT 1"
    assert r.postcondition is not None and r.postcondition.code == "non_empty"


def test_hard_violation_still_fails_closed_beside_a_soft_check():
    # Every candidate breaks the hard rule first (first violation wins), so
    # none is ever soft-only: no fallback, fail closed as before.
    eng = _engine(["SELECT *"], [NeedsLimit(), NonEmpty()], max_retries=2)
    r = eng.generate("q")
    assert r.ok is False and r.query == ""
    assert r.postcondition is not None and r.postcondition.code == "needs_limit"


def test_soft_fallback_still_passes_the_guardrail_gate():
    eng = _engine(["SELECT 1"], [NonEmpty()], allow=False, max_retries=1)
    r = eng.generate("q")
    assert r.ok is False and r.query == ""
    assert "tenant scope" in r.error


def test_violation_is_hard_by_default():
    assert PostconditionViolation(reason="r", suggested_hint="h").soft is False


def test_hard_check_after_a_soft_one_still_applies():
    # Regression: the soft check used to short-circuit the run, so a query
    # breaking the hard rule listed after it came back ok as a soft fallback.
    eng = _engine(["SELECT *"], [NonEmpty(), NeedsLimit()], max_retries=2)
    r = eng.generate("q")
    assert r.ok is False and r.query == ""
    assert r.postcondition is not None and r.postcondition.code == "needs_limit"


def test_soft_then_hard_reports_the_hard_correction():
    eng = _engine(["SELECT *", "SELECT * LIMIT 1 WHERE {}"], [NonEmpty(), NeedsLimit()])
    r = eng.generate("q")
    assert r.ok is True and r.retries == 1
    assert "Add LIMIT 100." in eng.provider.calls[1][1]  # the hard hint, not the soft one


def test_raising_check_after_a_soft_one_still_fails_closed():
    class Boom:
        code = "boom"

        def prompt_section(self):
            return ""

        def check(self, query, *, context):
            raise ValueError("kaboom")

    eng = _engine(["SELECT 1"], [NonEmpty(), Boom()], max_retries=1)
    r = eng.generate("q")
    assert r.ok is False and r.query == ""
    assert r.postcondition is not None and r.postcondition.code == "boom_error"


def test_soft_fallback_with_no_retries():
    eng = _engine(["SELECT 1"], [NonEmpty()], max_retries=0)
    r = eng.generate("q")
    assert r.ok is True and r.retries == 0 and r.query == "SELECT 1"
    assert r.postcondition is not None and r.postcondition.soft


def test_clean_last_candidate_beats_the_soft_fallback():
    eng = _engine(["SELECT 1 LIMIT 1", "SELECT * LIMIT 1 WHERE {}"], [NeedsLimit(), NonEmpty()])
    r = eng.generate("q")
    assert r.ok is True and r.query == "SELECT * LIMIT 1 WHERE {}"
    assert r.postcondition is None


def test_run_postconditions_prefers_hard_over_earlier_soft():
    ctx = PostconditionContext(schema_summary="", question="q", attempt=0)
    v = run_postconditions("SELECT *", [NonEmpty(), NeedsLimit()], ctx)
    assert v is not None and v.code == "needs_limit" and not v.soft
    v = run_postconditions("SELECT * LIMIT 1", [NonEmpty(), NeedsLimit()], ctx)
    assert v is not None and v.code == "non_empty" and v.soft
