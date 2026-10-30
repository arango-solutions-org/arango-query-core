# arango-query-core — Product Requirements Document

**Status:** Active development · **v0.2.0**
**Last updated:** 2026-08-23
**Owner:** arango-query-core maintainers

> This PRD governs the standalone `arango-query-core` distribution. The
> design source of record for the extraction that created this repo is
> [`arango-sparql-py/docs/architecture/proposals/nl-engine-extraction.md`](https://github.com/arango-solutions/arango-sparql-py/blob/main/docs/architecture/proposals/nl-engine-extraction.md)
> (Accepted — step 1 shipped 2026-07-16). Where this PRD and that
> proposal disagree, this PRD wins for current intent; the proposal wins
> for historical rationale. The consuming products' PRDs
> ([`arango-cypher-py/docs/PRD.md`](https://github.com/ArthurKeen/arango-cypher-py/blob/main/docs/PRD.md),
> arango-sparql-py) govern everything language-specific.

---

## 1. Executive summary

`arango-query-core` is the **shared conceptual-model and NL substrate**
for the ArangoDB query transpilers — `arango-cypher-py` (Cypher→AQL)
and `arango-sparql-py` (SPARQL→AQL) — and, through them, the
contextual-data-fabric's federated query engine. It exists so that
three consumers pin **one versioned artifact** instead of forking the
same code three ways.

It ships two things:

1. **The conceptual↔physical model contract** — `MappingBundle` and its
   resolver, wire-format parsing, hashing, OWL/Turtle round-trip, plus
   AQL fragment types, safe execution helpers, an extension registry,
   and the shared error base.
2. **A language-agnostic NL→query engine** — LLM provider abstraction
   (OpenAI / OpenRouter / Anthropic with prompt caching), BM25/dense
   few-shot retrieval, caller-supplied semantic postconditions, and the
   generate→validate→repair loop, with everything language-specific
   delegated to an eight-seam `QueryLanguageAdapter` protocol
   implemented *next to* each transpiler, never here.

### 1.1 Defining decisions (confirmed 2026-07-16)

- **Adapters stay in their transpiler repos.** `nl2cypher` lives in
  arango-cypher-py, `nl2sparql` in arango-sparql-py. Validating a
  generated query requires the transpiler stack; importing one here
  would create a dependency cycle or force consumers to install both
  query languages. Dependencies point one way: transpilers → this
  package.
- **Zero hard dependencies.** The core installs with no runtime deps;
  rdflib, requests, PyYAML, and rank_bm25 are lazily imported behind
  the `owl` and `nl` extras. A consumer that only needs
  `MappingBundle` pays for nothing else.
- **One distribution, not two.** The distribution name stays
  `arango-query-core` even though it now also carries the NL engine —
  conceptual-model identity is signaled at the subpackage level
  (`arango_query_core.nl`), not by minting a second artifact.
- **PyPI publication.** v0.1.0 was published 2026-07-17 (decision:
  claim the name while the `nl2cypher` re-point was in flight),
  superseding the proposal's publish-after-re-point gate. The gate now
  applies to *subsequent* releases: any seam-API change forced by the
  re-point ships as 0.2.0 with an explicit changelog break notice, and
  consumers keep pinning exact versions.
- **Behavioral reference.** The code was seeded verbatim from
  arango-cypher-py @ `5a1392b`; that repo's NL eval suite is the
  non-regression gate for the re-point.
- **0.2.0 ships the seam-6/7/8 break (2026-08-23).** Entity grounding
  (6), predicate grounding (7), and relationship-path grounding (8)
  join the protocol as *unconditionally called* methods — an adapter
  missing any of the eight raises `AttributeError` at prompt-build
  time. Also ships alongside: caller-supplied postconditions
  (`nl.postconditions`), the `nl.synthbank` shape catalog, and
  `DenseRetriever` (`dense` extra). Per §1.1's PyPI policy this is the
  explicit changelog-documented break. `nl2cypher` (pinned
  `<0.2.0` as of the step-2 re-point) and `nl2sparql` (adapter ships
  ungrounded seam-6/7 stubs, git-pinned pre-seam-8) each need a
  deliberate pin bump plus seam-8/postcondition implementation before
  moving past `0.1.0` — see §8.

## 2. Problem statement

Three engines were about to exist: the mature `nl2cypher` engine
(~7.5k LOC) in arango-cypher-py, a stub port in arango-sparql-py whose
TODO said "port the LLM provider abstraction, BM25 fewshot index, and
tenant guardrail", and the fabric's M5 WP-D1 plan to harvest nl2cypher
and "swap ~5 seams to emit SPARQL". Likewise `MappingBundle` shipped
inside arango-cypher-py's wheel, forcing the SPARQL leg to transitively
depend on the Cypher repo. Extracting the shared substrate while the
port was a stub and the harvest hadn't started was the cheapest moment
to prevent a three-way fork.

## 3. Goals / non-goals

### Goals

- One pinned artifact carrying the conceptual-model contract and the
  NL engine, consumed by both transpilers and the fabric.
- A small, stable public API: the package `__all__` plus the eight-seam
  `QueryLanguageAdapter` protocol. Stability of the seam list is the
  product; churn here multiplies into release + pin-bump work across
  every consumer (see §6).
- Safety invariants preserved from the source repo: bind parameters
  throughout, regex-validated collection names before any string
  interpolation into AQL, guardrail verdicts surfaced (never silently
  dropped).

### Non-goals

- **Per-language adapters** (grammar prompts, few-shot corpora,
  validators, repair rules, guardrail AST checks) — these live with
  their transpilers.
- **Entity resolution beyond the extracted interface**, transpilation
  itself, HTTP surfaces, CLIs, UIs — all product-repo concerns.
- **Schema inference** — `arangodb-schema-analyzer` remains canonical;
  this package only defines the bundle format it feeds.

## 4. Package architecture

| Module / subpackage | Purpose |
| --- | --- |
| `mapping` | `MappingBundle`, `MappingResolver` (incl. supertypes: `SUPERTYPE_STYLES`, `is_supertype`, `supertype_members`), `MappingSource`, `PropertyInfo`, `IndexInfo`, `RelationshipStats`; `mapping_from_wire_dict`, `mapping_hash`; collection-name grammar guard (`COLLECTION_NAME_RE`, `is_valid_collection_name`) |
| `owl_turtle` / `owl_rdflib` | OWL/Turtle ⇄ MappingBundle round-trip; rdflib parser behind the `owl` extra (import degrades to `None` without it) |
| `aql` | `AqlFragment`, `AqlQuery` value types |
| `exec` | `AqlExecutor` protocol, `safe_execute` (bind-var reference checking), `explain_aql` |
| `extensions` | `ExtensionRegistry` / `ExtensionPolicy` for `arango.*` extension functions |
| `errors` | `CoreError` shared base |
| `nl.engine` | `NLQueryEngine` — prompt assembly, few-shot retrieval, provider call, generate→validate→repair loop; `NLResult`. The query is the first fenced block anywhere in a response (prose around it is ignored), else the bare response. An adapter's optional `normalize` seam repairs each candidate before validation. `generate(user_hint=...)` appends a caller hint to the first user message only, leaving the cacheable system prefix alone. With `retry_provider_errors=True`, a provider call that raises uses one attempt of the budget (`NLResult.provider_errors`), and the last error is re-raised only if no attempt returned a response; the default propagates the first error. |
| `nl.providers` | `LLMProvider` protocol; OpenAI / OpenRouter / Anthropic implementations (Anthropic with prompt-cache system-block splitting); env-driven `get_llm_provider` |
| `nl.fewshot` | `FewShotIndex` over `(question, query)` pairs (legacy corpus keys accepted); `BM25Retriever` (rank_bm25, `nl` extra) and `DenseRetriever` (sentence-transformers, `dense` extra), selected via `mode="bm25"\|"dense"\|"auto"`; `cached_few_shot_index` memoizes the expensive dense build |
| `nl.grounding` | Seam 6/7 retrieval: `LabelIndex`/`GroundedEntity` (entity grounding) and `PredicateIndex`/`GroundedPredicate` (predicate/schema-convention grounding, renders IRI local names not labels), sharing one token-substring scorer |
| `nl.pathindex` | Seam 8: `ClassPathIndex`/`ClassPath` — bounded shortest predicate paths between two seam-6/7-anchored classes over the TBox's class-connectivity graph |
| `nl.postconditions` | Caller-supplied semantic invariants (`Postcondition`, `PostconditionContext`, `PostconditionViolation`, `run_postconditions`) enforced through the engine's own retry budget; fails closed on exhaustion, unless a valid candidate drew only a soft violation (`PostconditionViolation.soft`); the first such candidate is returned, with the violation on `NLResult.postcondition` |
| `nl.synthbank` | `SHAPE_CATALOG`/`ShapeTemplate` — 9 ontology-agnostic compositional query-shape templates for synthetic few-shot bank generation |
| `nl.seams` | `QueryLanguageAdapter` — the eight language-specific seams: (1) grammar prompt section, (2) few-shot corpus, (3) syntax validator, (4) repair rules keyed on validator errors, (5) guardrail checks, (6) entity grounding index, (7) predicate grounding index, (8) relationship-path grounding index. Optional, outside the protocol: `normalize(query) -> str`, a deterministic repair applied before validation. Plus `ValidationResult`, `GuardrailVerdict` |

## 5. Requirements

- **R1 — Dependency-free core.** `pip install arango-query-core` must
  succeed and import with zero runtime dependencies. Optional
  capability arrives only via extras: `[owl]` (rdflib), `[nl]`
  (requests, PyYAML, rank_bm25).
- **R2 — Python ≥ 3.11**, tested on 3.11 and 3.12 in CI.
- **R3 — Safe-by-construction AQL surfaces.** No user input is ever
  string-interpolated into AQL; collection names embedded into AQL are
  regex-validated first (`is_valid_collection_name`); `safe_execute`
  cross-checks that declared bind vars are actually referenced.
- **R4 — Language-agnostic engine.** Nothing under `arango_query_core`
  may import a transpiler or assume a target query language beyond the
  adapter's `language` tag. The engine treats validation and guardrail
  results as opaque adapter verdicts.
- **R5 — Deterministic contract types.** `mapping_hash` over a bundle
  is stable across processes (canonical JSON) so consumers can use it
  for cache keys and change detection.
- **R7 — Label hierarchies (supertypes).** A supertype is a label whose
  instances are the instances of other labels (LDBC's `Message` is every
  `Post` and every `Comment`).
  - **Styles.** It is mapped with one of two entity styles (`SUPERTYPE_STYLES`):
    - `MULTI_LABEL`: the members share one collection, and documents list
      their labels in an array field (`labelsField`, `labelValue`).
    - `UNION`: the members have collections of their own (`members`).
  - **Members** come from the physical `members` list or the conceptual
    entity's `subtypes`.
  - **Resolver.** `MappingResolver.supertype_members()` flattens nested
    supertypes, and refuses unknown members and cycles (`INVALID_MAPPING`).
    A supertype without properties of its own has its members' properties.
    `edge_constrains_target` treats a supertype target as guaranteed when
    every class an edge endpoint can be is a member.
  - **New styles fail closed.** Supertypes get styles of their own, so a
    consumer that does not handle them rejects the mapping instead of
    misreading it.
  - **OWL.** OWL/Turtle round-trips the hierarchy as `rdfs:subClassOf`.
- **R6 — Quality gates.** ruff (lint + format), mypy, and pytest run in
  CI on every push/PR; all three must pass before a release is tagged.

## 6. Versioning & release policy

Consumed as a **pinned artifact** under the fabric's pin-everything
policy (contextual-data-fabric CC-9): consumers pin exact versions and
re-run their golden/eval suites on every bump — arango-cypher-py's NL
eval suite and arango-sparql-py's `RUN_EVAL=1` harness are the gates.
Consequences:

- Every release is deliberate: a version bump costs a release plus
  2–3 downstream pin bumps. Batch changes; keep the seam API stable.
- Tag releases `vX.Y.Z`; maintain `CHANGELOG.md` (Keep-a-Changelog
  style). Pre-1.0, minor bumps may break the API but the changelog
  must say so explicitly.
- Published on PyPI as of v0.1.0 (2026-07-17); consumers pin exact
  PyPI versions (git tags remain as a fallback pin).

## 7. Current state (2026-08-23)

v0.2.0, published on PyPI. Since 0.1.0: seams 6-8 (entity/predicate/
path grounding), caller-supplied postconditions, the `nl.synthbank`
shape catalog, `DenseRetriever`, and the predicate IRI local-name fix.
146 tests (up from 78), ruff + mypy + pytest green in CI, sdist/wheel
build clean.

## 8. Roadmap (extraction steps 2–4)

1. ~~Carve `arango-query-core` into its own repo/distribution.~~ ✅
2. **Re-point `nl2cypher`** at this core — **done** (arango-cypher-py
   `56cdb14`), pinned `>=0.1.0,<0.2.0`. Its adapter does not yet
   implement seams 6-8 or postconditions; bumping past the `<0.2.0`
   ceiling needs that work first, gated by its NL eval suite.
3. **Implement `nl2sparql`** as the second adapter in arango-sparql-py
   — its adapter implements seams 6/7 as ungrounded stubs but is
   git-pinned pre-seam-8; seam-8 + postcondition adoption and the pin
   bump to 0.2.0 are a deliberate CC-9 step (re-run `RUN_EVAL=1`
   goldens) still ahead.
4. **Hand fabric WP-D1** the core + adapter pins.

Later (from arango-cypher-py PRD §12.3): resolver-adjacent pieces may
migrate here once both transpilers consume the published artifact.

## 9. Risks

- **Seam churn.** Every engine change is a release + 2–3 pin bumps.
  Mitigation: the public surface is deliberately the seam list — small
  and stable.
- **Guardrails don't fully extract.** AST validators are
  language-specific; the split is interface-in-core
  (`GuardrailVerdict`), implementation-in-adapter. A weak adapter
  guardrail is a consumer-repo bug, but this package must never make
  one easy to bypass (verdicts gate returns; refusals carry reasons).
- **Single-maintainer coordination** across three consuming repos.
  Mitigation: this PRD + the changelog are the coordination surface.
