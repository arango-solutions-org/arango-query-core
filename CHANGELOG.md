# Changelog

All notable changes to `arango-query-core` are documented here, per
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Consumers pin
exact versions (contextual-data-fabric CC-9) and re-run their golden /
eval suites on every bump — pre-1.0, **minor** bumps may break the API
and this file must say so explicitly.

## [Unreleased]

### Added

- `LABELS` entity style: `{"style": "LABELS", "collectionName": "nodes", "labelsField": "labels"}`
  keeps a node's labels in an array field of a shared collection, so a node can
  carry several labels (Neo4j-shaped data; the label predicate is
  `"Label" IN doc.labels`). `resolve_entity` returns it unchanged,
  `schema_summary` reports `labelsField`, and the OWL Turtle export/import and
  the rdflib import carry `phys:labelsField`. Shared with arango-gql-gateway,
  so a mapping from either tool works in either. Distinct from `MULTI_LABEL`
  (0.2.5, below): `LABELS` is a plain entity whose own declared name is the
  array value to match; `MULTI_LABEL` is a supertype matching a separate,
  explicit `labelValue` across its members' shared collection.
- `CATCH_ALL_KEY` (`"*"`): a `*` key under `entities` or `relationships`
  resolves any label or type with no entry of its own, after exact and
  normalized lookup. It is not listed by `all_entity_labels` /
  `all_relationship_types`. Bundles without a `*` key behave as before.

## [0.2.6] - 2026-10-29

### Added

- **Temporal property encodings.** A property may declare how its temporal
  values are stored, `"encoding": "epoch_ms"` (integer milliseconds since the
  epoch) or `"iso8601"` (an ISO-8601 string), read into
  `PropertyInfo.encoding` (empty when not declared) and carried by
  `schema_summary()`. Any other value is refused with `INVALID_MAPPING`.
  The vocabulary is `TEMPORAL_ENCODINGS`. It is a physical detail, like
  `field`, so OWL/Turtle does not carry it. arango-cypher-py lowers temporal
  accessors, functions and arithmetic from it (LDBC WP-41).

## [0.2.5] - 2026-10-26

### Added

- **Supertypes (label hierarchies).** A supertype is a label that stands for
  other labels, such as LDBC's `Message`, which is every `Post` and every
  `Comment`. It is mapped with one of two new entity styles,
  `SUPERTYPE_STYLES`:
  - `MULTI_LABEL`: the members share a collection and list their labels in
    an array field (`labelsField`, `labelValue`);
  - `UNION`: the members have their own collections (`members`).
- **New resolver methods.** `MappingResolver.is_supertype()` and
  `supertype_members()`. The latter flattens nested supertypes and refuses
  unknown members and cycles with `INVALID_MAPPING`. A supertype without
  properties of its own has its members' properties.
- **`edge_constrains_target`** treats a supertype target as guaranteed when
  every class an edge endpoint can be is a member: `HAS_CREATOR`'s domain
  `[Post, Comment]` guarantees `Message`.
- **OWL/Turtle** writes and reads the hierarchy as `rdfs:subClassOf`.
  `turtle_to_mapping` no longer invents a `collectionName` for a `UNION`
  supertype. The rdflib parser does not read `rdfs:subClassOf` yet.

The change is additive: mappings without the new styles resolve exactly as
before. A consumer that switches on `style` and does not handle the new
values rejects them rather than misreading them.

## [0.2.4] - 2026-10-21

### Added

- `QueryLanguageAdapter` may define `normalize(query) -> str`, an optional seam
  `NLQueryEngine` calls on every candidate after the fence is stripped and
  before `validate`. Use it for a deterministic repair that would otherwise
  cost an LLM retry; nl2cypher uses it to rewrite labels the schema does not
  have. What it returns is validated, quoted in retries and returned. A
  `normalize` that raises is logged and the candidate kept. It is not a
  protocol member, so existing adapters are unaffected. #8
- `NLQueryEngine.generate(..., user_hint="")` appends a caller hint to the
  first attempt's user message only, for example "your previous query
  failed with …" when re-asking. It leaves the system prompt (the cacheable
  prefix), few-shot retrieval and postcondition context untouched. #10
- `NLQueryEngine(retry_provider_errors=True)`: a provider call that raises (a
  timeout, a 5xx) uses one attempt of `max_retries`, and the loop goes on.
  `NLResult.provider_errors` counts them. If no attempt returned a response,
  the last exception is re-raised. The default (`False`) keeps today's
  behaviour: the first exception propagates. #9

### Fixed

- The engine finds the query in the first fenced block anywhere in the
  response. It used to strip a fence only when the response started with
  one, so "Here is the query: ```…```" failed validation and spent a retry.
  A response that opens a fence without closing it yields what follows the
  fence, and a response with no fence is returned as is, as before. When a
  response has two fenced blocks, the first is taken; it used to be
  everything from the first opening fence to the last closing one. #7

## [0.2.3] - 2026-10-21

### Added

- `PostconditionViolation.soft` (default `False`): a soft violation is
  retried like any other, but when the retry budget is spent
  `NLQueryEngine.generate` returns the first valid candidate whose only
  objection was soft, instead of failing closed. The candidate still passes
  guardrails, and the violation stays on `NLResult.postcondition` (`ok` True
  with a non-None `postcondition` marks a soft fallback). For checks such as
  "the query returned no rows", where empty is sometimes the right answer.
  `run_postconditions` now returns the first *hard* violation ahead of any
  soft one, so a hard check listed after a soft one still applies. Additive;
  existing checks are unaffected. Part of #4.

## [0.2.2] - 2026-10-21

### Fixed

- **Traversals no longer hint an array-expanded edge index.** `resolve_indexes`
  treated any edge index led by `_from` / `_to` as a vertex-centric index, so
  `[_from, type, toLabels[*]]` (r2g's opt-in `LpgLayout.index_edge_labels`
  shape) could become arango-cypher-py's traversal `indexHint`. A persistent
  index stores one entry per array element and a traversal scans all of them,
  so the hinted traversal returned each edge once per label -- inflated counts,
  no error (measured on ArangoDB 3.12.9: 20x the rows at one hop, 400x at two).
  Such an index is now never `vci`, even with an explicit `vci: true` flag. With
  only that index, arango-cypher-py reports a missing VCI instead of hinting it;
  next to a flat `[_from, type]` index, the flat one is hinted, whatever the
  order.
- **The schema analyzer's verdict is honored.** When a relationship mapping
  carries arangodb-schema-analyzer's `vci.indexLevel.indexes[].traversalSafe:
  false` (analyzer 0.15.2+), that index is not traversal-safe, whatever its
  shape. Older exports carry no verdict; the local array-expansion check still
  applies to them.

### Added

- `IndexInfo.traversal_safe` (default `True`): False when a traversal must not
  use the index. Additive; existing callers are unaffected.

## [0.2.1] - 2026-10-16

### Fixed

- **OWL import keeps property names.** `owl_turtle.turtle_to_mapping` cut
  every datatype-property name at its last underscore — a rule meant for the
  `:<REL>_<prop>` relationship properties `mapping_to_turtle` writes — so
  `file_name` imported as `name` and `account_id` as `id`, including on a
  round trip of this library's own export. Entity properties now keep their
  full name; relationship properties lose exactly their `<REL>_` prefix.
- **Shared fields reach every class.** Both importers (`turtle_to_mapping`
  and the rdflib-based `parse_owl_with_rdflib`) dropped a property whose
  `rdfs:domain` is an `owl:unionOf` list — arangodb-schema-analyzer
  0.15.1's form for a field several classes share — and the rdflib one read
  only the first of several domains. Each member / domain now gets the field,
  and the anonymous union class is no longer imported as an entity.
- **Stored field names.** `phys:attributeName` (analyzer 0.15.1) now maps a
  property to its stored field in the physical mapping
  (`properties.<name>.field`), so a sanitized or renamed field (`file-name`,
  `page_count`) resolves to the attribute that holds it.

## [0.2.0] - 2026-08-23

### BREAKING

- `QueryLanguageAdapter` grows from five seams to **eight** — seams 6-8
  (`grounding_index`/`grounding_prompt_section`,
  `predicate_index`/`predicate_prompt_section`,
  `path_index`/`path_prompt_section`) are now called *unconditionally*
  by `NLQueryEngine._system_prompt`. An adapter missing any of the
  eight methods raises `AttributeError` at prompt-build time — there
  is no `hasattr` fallback. Existing adapters must implement the three
  new methods (returning `None` from the `*_index()` half is a valid
  "run ungrounded" no-op, mirroring seam 2's `few_shot_index() -> None`
  contract) before bumping their pin past this release.

### Added

- **Seam 6 — entity/instance grounding** (`nl.grounding.LabelIndex` /
  `GroundedEntity`): retrieves matching instances from the target data
  and injects their exact IDs into the prompt, so the model references
  a specific entity instead of guessing a name-literal match. Measured
  lifting execution-graded accuracy from 12.2% to 24.5% on a CK25 spike
  (McNemar p=0.031, 0 regressions).
- **Seam 7 — predicate/schema-convention grounding**
  (`nl.grounding.PredicateIndex` / `GroundedPredicate`): a distilled
  usage cheat-sheet for TBox predicates (domain/range/kind/derived
  usage shape) instead of making the model infer conventions from raw
  `rdfs:domain`/`rdfs:range` triples. Renders the predicate's **IRI
  local name** as the identifier to emit (human label demoted to a
  gloss) — fixes models inventing predicates from the label instead of
  using the real vocabulary term (9/10 previously-invented-predicate
  CK25 cases corrected, 0 regressions).
- **Seam 8 — relationship-path grounding** (`nl.pathindex.ClassPathIndex`
  / `ClassPath`): bounded shortest predicate paths between two
  seam-6/7-anchored classes over the TBox's own class-connectivity
  graph — fixes the model grounding the right entity but inventing a
  predicate path that executes to an empty result set.
- **Caller-supplied postconditions** (`nl.postconditions`): a
  language-agnostic mechanism for enforcing semantic invariants a
  generated query must satisfy — the class of bug that `validate()`
  and `guardrails()` both miss (a well-formed, allowed query that is
  nonetheless semantically wrong). Rides the same generate→validate→
  retry budget; fails closed on exhaustion rather than returning a
  plausible-but-wrong query. `NLQueryEngine` gains `postconditions` /
  `postcondition_mapping`.
- **`nl.synthbank`** — a query-first synthetic few-shot template
  catalog (`SHAPE_CATALOG`, `ShapeTemplate`): 9 ontology-agnostic
  compositional query shapes (lookup, value_object, category_filter,
  scalar_count, grouped_aggregation, top_n, offset, negation, two_hop),
  promoted out of arango-sparql-py's eval-side bank generator so
  onboarding callers and a future Cypher front-end share one catalog.
- **`DenseRetriever`** (`nl.fewshot`, `dense` extra): cosine-similarity
  few-shot retrieval over sentence embeddings, as an alternative to
  BM25. Pinned model + revision hash for reproducible ranking.
  `FewShotIndex.from_corpus_files(..., mode=...)` dispatches between
  `"dense"` (hard-raises if unavailable), `"bm25"`, and `"auto"`
  (dense → BM25 → no-op, never raises — the default, preserving prior
  behavior for existing callers). `cached_few_shot_index` memoizes the
  expensive dense build per (bank path, mode).
- New `dense` extra (`sentence-transformers>=3.0`); `py.typed` marker.

### Fixed

- `PredicateIndex.format_prompt_section` rendered a predicate's
  `rdfs:label` as the identifier to emit; now renders the IRI local
  name (see seam 7 above).

146 tests (up from 78 at 0.1.0); ruff (lint + format) and mypy clean.

## [0.1.0] - 2026-07-17

Initial standalone release — extraction step 1 (see `docs/PRD.md`).

### Added

- Conceptual↔physical model contract, seeded verbatim from
  arango-cypher-py @ `5a1392b`: `MappingBundle`, `MappingResolver`,
  `mapping_from_wire_dict`, `mapping_hash`, collection-name grammar
  guard, OWL/Turtle ⇄ bundle round-trip (`owl` extra), AQL fragment
  types, `safe_execute` / `explain_aql`, extension registry,
  `CoreError` base — with their five test modules.
- `arango_query_core.nl` — language-agnostic NL→query engine:
  `NLQueryEngine` (generate→validate→repair loop), LLM providers
  (OpenAI / OpenRouter / Anthropic with prompt caching), BM25 few-shot
  retrieval over `(question, query)` pairs (`nl` extra), and the
  five-seam `QueryLanguageAdapter` protocol (`nl.seams`).
- Zero hard runtime dependencies; `owl` / `nl` / `dev` extras.
- CI: ruff (lint + format), mypy, pytest on Python 3.11 / 3.12.

[0.2.0]: https://github.com/ArthurKeen/arango-query-core/releases/tag/v0.2.0
[0.1.0]: https://github.com/ArthurKeen/arango-query-core/releases/tag/v0.1.0
