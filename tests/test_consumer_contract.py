"""Every name a consumer imports from this package still imports.

The public surface is wider than ``__all__``: the consumers' non-test code
imports the names below, one of them underscore-prefixed, and older consumer
checkouts import two more (``PRE_CYPHER_113``). Removing or renaming one breaks
that consumer at import, on its next install inside its version band, with no
pin change on its side. So a name leaves these tables only after the consumer
has stopped importing it, and a private name stays importable (as a deprecated
alias if need be) for at least one release after that.

``CONSUMER_CONTRACT`` was built by parsing, with ``ast``, every non-test ``.py``
file on the ``main`` branch of arango-cypher-py (``a8e2049``), arango-sparql-py
(``4b11d07``) and contextual-data-fabric (``789486d``), 2026-11-23. Re-run
that search before deleting or renaming any name here.
"""

from __future__ import annotations

import warnings

import pytest

from arango_query_core.nl import OpenAIProvider, get_llm_provider

CYPHER, SPARQL, CDF = "arango-cypher-py", "arango-sparql-py", "contextual-data-fabric"

#: (module, name) -> consumers that import it.
CONSUMER_CONTRACT: dict[tuple[str, str], tuple[str, ...]] = {
    ("arango_query_core", "AqlQuery"): (CYPHER,),
    ("arango_query_core", "CoreError"): (CYPHER,),
    ("arango_query_core", "ExtensionPolicy"): (CYPHER,),
    ("arango_query_core", "ExtensionRegistry"): (CYPHER,),
    ("arango_query_core", "IndexInfo"): (CYPHER,),
    ("arango_query_core", "MappingBundle"): (CYPHER,),
    ("arango_query_core", "MappingResolver"): (CYPHER,),
    ("arango_query_core", "MappingSource"): (CYPHER,),
    ("arango_query_core", "SUPERTYPE_STYLES"): (CYPHER,),
    ("arango_query_core", "is_valid_collection_name"): (CYPHER,),
    ("arango_query_core", "mapping_from_wire_dict"): (CYPHER,),
    ("arango_query_core", "mapping_hash"): (CYPHER,),
    ("arango_query_core", "safe_execute"): (CYPHER,),
    ("arango_query_core.errors", "CoreError"): (CYPHER,),
    ("arango_query_core.exec", "AqlExecutor"): (CYPHER,),
    ("arango_query_core.exec", "explain_aql"): (CYPHER,),
    ("arango_query_core.mapping", "MappingBundle"): (CYPHER,),
    ("arango_query_core.mapping", "MappingResolver"): (CYPHER,),
    ("arango_query_core.mapping", "mapping_from_wire_dict"): (CYPHER,),
    ("arango_query_core.owl_turtle", "mapping_to_turtle"): (CYPHER,),
    ("arango_query_core.owl_turtle", "turtle_to_mapping"): (CYPHER,),
    ("arango_query_core.nl", "AnthropicProvider"): (CYPHER,),
    ("arango_query_core.nl", "BM25Retriever"): (CYPHER,),
    ("arango_query_core.nl", "ClassPathIndex"): (SPARQL,),
    ("arango_query_core.nl", "FewShotIndex"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl", "GuardrailVerdict"): (SPARQL,),
    ("arango_query_core.nl", "HostExtensions"): (CDF,),
    ("arango_query_core.nl", "HostPredicate"): (CDF,),
    ("arango_query_core.nl", "LLMProvider"): (CYPHER,),
    ("arango_query_core.nl", "LabelIndex"): (SPARQL,),
    ("arango_query_core.nl", "NLQueryEngine"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl", "OpenAIProvider"): (CYPHER,),
    ("arango_query_core.nl", "OpenRouterProvider"): (CYPHER,),
    ("arango_query_core.nl", "Postcondition"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl", "PostconditionContext"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl", "PostconditionViolation"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl", "PredicateIndex"): (SPARQL,),
    ("arango_query_core.nl", "PropertyFact"): (CDF,),
    ("arango_query_core.nl", "Retriever"): (CYPHER,),
    ("arango_query_core.nl", "ValidationResult"): (SPARQL,),
    ("arango_query_core.nl", "cached_few_shot_index"): (SPARQL,),
    ("arango_query_core.nl", "get_llm_provider"): (CYPHER, SPARQL),
    ("arango_query_core.nl", "host_prompt_section"): (CDF,),
    ("arango_query_core.nl", "postcondition_prompt_sections"): (CYPHER,),
    ("arango_query_core.nl", "providers"): (CYPHER,),
    ("arango_query_core.nl", "run_postconditions"): (CYPHER,),
    ("arango_query_core.nl", "split_system_for_anthropic_cache"): (CYPHER,),
    ("arango_query_core.nl.engine", "NLQueryEngine"): (SPARQL,),
    ("arango_query_core.nl.fewshot", "BM25Retriever"): (CYPHER,),
    ("arango_query_core.nl.fewshot", "FewShotIndex"): (CYPHER,),
    ("arango_query_core.nl.fewshot", "Retriever"): (CYPHER,),
    ("arango_query_core.nl.grounding", "GroundedEntity"): (CYPHER, CDF),
    ("arango_query_core.nl.grounding", "GroundedPredicate"): (CYPHER,),
    ("arango_query_core.nl.grounding", "LabelIndex"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl.grounding", "PredicateIndex"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl.grounding", "_local_name"): (SPARQL,),
    ("arango_query_core.nl.pathindex", "ClassPathIndex"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl.postconditions", "Postcondition"): (SPARQL,),
    ("arango_query_core.nl.postconditions", "PostconditionContext"): (CYPHER,),
    ("arango_query_core.nl.postconditions", "PostconditionViolation"): (CYPHER,),
    ("arango_query_core.nl.providers", "LLMProvider"): (CYPHER,),
    ("arango_query_core.nl.providers", "get_llm_provider"): (CYPHER,),
    ("arango_query_core.nl.seams", "GuardrailVerdict"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl.seams", "ValidationResult"): (CYPHER, SPARQL, CDF),
    ("arango_query_core.nl.synthbank", "RELATIONAL_SHAPES"): (CYPHER,),
    ("arango_query_core.nl.synthbank", "SHAPE_CATALOG"): (CYPHER,),
    ("arango_query_core.nl.synthbank", "ShapeTemplate"): (CYPHER,),
}

#: Imported by arango-cypher-py before arango-cypher#113 (merged 2026-11-23), so
#: its checkouts from before then still import them. Per the rule above, keep
#: them through 0.2.11; the first release after that may drop them.
PRE_CYPHER_113: tuple[tuple[str, str], ...] = (
    ("arango_query_core.nl.fewshot", "_NoopRetriever"),
    ("arango_query_core.nl.providers", "_get_default_provider"),
)

IMPORTED: dict[tuple[str, str], tuple[str, ...]] = {
    **CONSUMER_CONTRACT,
    **{pair: (f"{CYPHER} before arango-cypher#113",) for pair in PRE_CYPHER_113},
}


@pytest.mark.parametrize(("module", "name"), sorted(IMPORTED), ids=lambda v: v)
def test_consumer_imported_name_still_imports(module: str, name: str) -> None:
    consumers = ", ".join(IMPORTED[(module, name)])
    try:
        exec(f"from {module} import {name}", {})
    except ImportError as exc:
        pytest.fail(f"`from {module} import {name}` fails, and {consumers} imports it: {exc}")


def test_get_default_provider_is_a_deprecated_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    from arango_query_core.nl.providers import _get_default_provider

    for key in ("LLM_PROVIDER", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with pytest.warns(DeprecationWarning, match="get_llm_provider"):
        provider = _get_default_provider()
    assert isinstance(provider, OpenAIProvider)
    assert type(provider) is type(get_llm_provider())

    monkeypatch.delenv("OPENAI_API_KEY")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        # Re-reads the environment on each call, like the public function.
        assert _get_default_provider() is None
