---
name: code-quality
description: Audit arango-query-core for unnecessary complexity, duplicated logic, and reuse misses — a simplicity/necessity-ladder review, distinct from pr-review's correctness and mergeability focus. Use for a whole-repo audit or a diff/PR-scoped quality pass, or when asked whether something "needs to be this complicated" or could reuse existing logic.
---

# Code quality (arango-query-core)

## Invocation
`/code-quality audit` — scan the whole `arango_query_core` package for
unnecessary complexity, duplicated logic, and reuse misses.
`/code-quality review <diff | branch | PR number>` — same lens, scoped to a
change instead of the whole repo.

## Purpose
`pr-review` asks "is this safe to merge" — mergeability, the CI gate,
hand-traced correctness. This skill asks a different question: "does this
need to exist, and is it the simplest form that works." It's a simplicity and
reuse audit, modeled on the necessity ladder from the `ponytail` skill
(github.com/DietrichGebert/ponytail), adapted to this repo's small,
zero-dependency-core package. Run it alongside `pr-review`, not instead of
it — a change can be perfectly safe to merge and still be more complicated
than it needs to be.

## The necessity ladder
For any new or existing piece of logic, check in this order and stop at the
first rung that holds:

1. Does this need to exist at all?
2. Is equivalent logic already somewhere in this codebase? Grep for the
   concept before writing it.
3. Could the standard library do it?
4. Is there an already-installed dependency that does it?
5. Can it collapse to one line?
6. Is it the minimum that works?

This repo already has a model reuse chain worth following: `grounding.py`'s
`_sanitize_label`/`_CONTROL_CHARS_RE`/`_WHITESPACE_RUN_RE` → `host.py`'s
`_clean`/`LINE_MAX`, built on those primitives → `engine.py`'s
`generate(context=...)`, reusing `host.py`'s `_clean`/`LINE_MAX` rather than
writing a fourth sanitizer. Three PRs, three different authors, each checked
for reuse before writing new code. That's rung 2 working as intended.

## Never cut
Do not flag or suggest simplifying away trust-boundary validation, security
checks, input sanitization (e.g. `nl.host._clean`, `nl.grounding._sanitize_label`),
or data-loss handling, even when it reads as "extra" code for what the rest
of the function does. These are explicitly out of scope for simplification
pressure.

## Whole-repo audit protocol (`/code-quality audit`)
1. Read every file under `arango_query_core/` in full — skip `tests/` and
   `docs/`, this is a source-quality audit. The package is small enough
   (~4600 lines across 18 files as of the first audit) to read end to end
   rather than sample.
2. Map entry points and real data flow: which modules sit on paths many
   callers depend on (`mapping.py`'s `MappingResolver`, `nl/engine.py`'s
   `NLQueryEngine`) versus edge plumbing (`extensions.py`, `errors.py`).
3. For each file, check against the necessity ladder. Flag: logic
   reimplemented when it already exists elsewhere in the package, duplicated
   blocks that should collapse to one shared helper, dead code (a symbol with
   zero callers across the package and test suite, not exported from an
   `__init__.py`), and abstractions broader than any real caller needs.
4. Rank findings by reach × impact, not file order — a duplication on the
   central `MappingResolver` path outranks one in a UI-only summary method.
5. Also report genuine positive examples (reuse chains followed correctly,
   a deliberate decision not to merge two similar-looking things because it
   would cost something real). This is a comparative audit, not a complaint
   list — the point is showing where the codebase already holds the line as
   much as where it doesn't.

## Diff-scoped protocol (`/code-quality review <target>`)
Same ladder, applied only to the lines a diff/PR touches plus the code it
calls into — not the whole repo. Use when assessing one PR's quality,
alongside or after a `pr-review` correctness pass.

## Report
State file and line range, what the code does today, which rung it should
sit on instead and why, and a reach/impact ranking. Lead with the
highest-reach finding, not the first one found.

## History
- **2026-11-20, first whole-repo audit.** Found: `MappingResolver.resolve_entity`/
  `resolve_relationship` duplicate their lookup-and-fallback-hint body
  (`mapping.py`); `resolve_properties`/`resolve_indexes` duplicate the same
  dict-lookup pattern (`mapping.py`); three independent XSD-type mapping
  tables for the same ~8 types across `owl_turtle.py` and `owl_rdflib.py`;
  two inconsistent `_local_name` implementations (`grounding.py` vs
  `owl_rdflib.py`) that disagree on a URI containing both `#` and `/`; a
  repeated conditional-merge pattern in `schema_summary()`. All still open.
  Also found and fixed two dead symbols with zero callers anywhere
  (`providers.py`'s `_get_default_provider`/`_DEFAULT_PROVIDER*`,
  `synthbank.py`'s `_SHAPES_BY_NAME`) — removed in commit `5d88e9c`.
