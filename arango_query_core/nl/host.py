"""What a host's engine accepts beyond the target language, told to the NL engine.

A host may run the generated query on an engine that accepts more than the language
the adapter teaches: a registered predicate such as a text match, per-property facts
(a datatype, whether it may be sorted, whether it is always present), and limits of
its own. Seam 1 (``grammar_prompt_section``) is adapter-owned and whole, so without
this module the only way to say any of that is to rewrite the grammar.

* :class:`HostPredicate` — a predicate the host's engine accepts in addition to the
  language, with the properties it may be used on and what not to write instead.
* :class:`PropertyFact` — facts about one property that change which queries succeed.
  Field names follow :class:`arango_query_core.mapping.PropertyInfo`.
* :class:`HostExtensions` — the declaration: predicates, rules (the host's limits that
  are not about one property, one sentence each) and property facts.
* :func:`host_prompt_section` — the system-prompt section; ``""`` when nothing is
  declared. :class:`~arango_query_core.nl.engine.NLQueryEngine` renders it straight
  after the grammar section, inside the cacheable prefix, when its
  ``host_extensions`` field is set.

Rules this module holds:

* **Nothing supplied, nothing changed.** ``None``, an empty declaration, a predicate
  offered on no property, a fact that declares nothing and a blank rule all render
  ``""``, and the engine's system prompt is then byte-identical to one built without
  the field.
* **It adds to the grammar and never lifts its rules.** One fixed sentence,
  :data:`HOST_RULE`, says so in the section itself.
* **Host strings cannot open a prompt section.** Every piece of host prose is
  collapsed to one line, capped at :data:`LINE_MAX` characters, and has its runs of
  ``#`` collapsed to one, so it can neither start a heading nor carry the
  ``## Examples`` marker that
  :func:`~arango_query_core.nl.providers.split_system_for_anthropic_cache` splits at.
* **Names are validated when built.** Predicate names, namespaces and
  ``Class.property`` entries that are not plain names raise :class:`ValueError`, so
  they never reach the prompt.

Checking that the model *used* a declared predicate is language-specific and stays
with the adapter or a host :class:`~arango_query_core.nl.postconditions.Postcondition`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .grounding import _CONTROL_CHARS_RE, _WHITESPACE_RUN_RE

__all__ = [
    "FACTS_HEADER",
    "HOST_HEADER",
    "HOST_RULE",
    "LINE_MAX",
    "RULES_HEADER",
    "HostExtensions",
    "HostPredicate",
    "PropertyFact",
    "host_prompt_section",
]

HOST_HEADER = "## What this engine accepts beyond the rules above"
HOST_RULE = (
    "These predicates are allowed in addition to the rules above, and only on the "
    "properties listed for each. Everything the rules above forbid stays forbidden."
)
"""The one fixed sentence: the section adds to the grammar, never overrides it."""
RULES_HEADER = "## Limits this engine adds to the rules above"
FACTS_HEADER = "## Facts about properties"

LINE_MAX = 600
"""Cap on one piece of host prose. Host prose is a sentence or two, so the cap is
larger than a grounding label's (200), which cut a predicate's meaning short."""

_NAME_RE = re.compile(r"^[A-Za-z_][\w-]*(?::[A-Za-z_][\w-]*)?$")
"""A plain or prefixed name, e.g. ``ex:textMatch``."""
_PROPERTY_RE = re.compile(r"^[A-Za-z_][\w-]*\.[A-Za-z_][\w-]*$")
"""A ``Class.property`` name."""
_IRI_FORBIDDEN = frozenset('<>"{}|^`\\ \t\r\n')
_HASH_RUN_RE = re.compile(r"#{2,}")


def _clean(text: str) -> str:
    """One line of host prose that cannot open a prompt section.

    Control characters and whitespace runs collapse to one space (the rule
    ``grounding._sanitize_label`` applies to labels), runs of ``#`` collapse to one
    so no heading or ``## Examples`` marker survives, and the result is capped at
    :data:`LINE_MAX`. Clean single-line prose without ``##`` passes through unchanged.
    """
    cleaned = _WHITESPACE_RUN_RE.sub(" ", _CONTROL_CHARS_RE.sub(" ", text))
    return _HASH_RUN_RE.sub("#", cleaned).strip()[:LINE_MAX].rstrip()


@dataclass(frozen=True)
class HostPredicate:
    """A predicate the host's engine accepts in addition to the language."""

    name: str
    """As the model writes it, e.g. ``ex:textMatch``."""
    iri: str
    """The namespace the name's prefix stands for, for the PREFIX line; ``""`` when the
    name has no prefix."""
    meaning: str
    """One sentence, in the host's words."""
    usage: str
    """One example in the target language. It may span lines; each line is cleaned."""
    applies_to: tuple[str, ...] = ()
    """``Class.property`` names it may be used on. Empty means it is offered nowhere,
    and a predicate offered nowhere is left out of the prompt."""
    instead_of: str = ""
    """What not to write instead, e.g. "an exact = comparison on these properties"."""
    restrictions: tuple[str, ...] = ()
    """Where the host's engine refuses it, one short clause each, e.g. "inside OPTIONAL"."""

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"host predicate name {self.name!r} is not a plain or prefixed name")
        if ":" in self.name and not self.iri:
            raise ValueError(f"host predicate {self.name!r} has a prefix but no namespace iri")
        if any(ch in _IRI_FORBIDDEN for ch in self.iri):
            raise ValueError(f"host predicate {self.name!r}: namespace iri {self.iri!r} is malformed")
        bad = [p for p in self.applies_to if not _PROPERTY_RE.match(p)]
        if bad:
            raise ValueError(f"host predicate {self.name!r}: not Class.property names: {bad}")
        if not _clean(self.meaning) or not _clean(self.usage):
            raise ValueError(f"host predicate {self.name!r} needs a meaning and a usage example")

    def prompt_lines(self) -> list[str]:
        """The predicate's lines in the section."""
        prefix = self.name.split(":", 1)[0] if ":" in self.name else ""
        lines = [f"- {self.name} — {_clean(self.meaning)}"]
        if prefix:
            lines.append(f"  Declare it with: PREFIX {prefix}: <{self.iri}>")
        lines.append(f"  Use it only on: {', '.join(self.applies_to)}")
        instead_of = _clean(self.instead_of)
        if instead_of:
            lines.append(f"  On those properties use it instead of {instead_of}.")
        restrictions = [r for r in map(_clean, self.restrictions) if r]
        if restrictions:
            lines.append("  Refused: " + "; ".join(restrictions) + ".")
        lines.append("  Example:")
        lines += [f"    {row}" for row in map(_clean, self.usage.splitlines()) if row]
        return lines


@dataclass(frozen=True)
class PropertyFact:
    """Facts about one property. ``None`` or ``""`` means "not declared" and renders nothing."""

    entity: str
    property: str
    type: str = ""
    """The value's datatype in the host's words, e.g. ``date``."""
    required: bool | None = None
    """Whether every entity has the property."""
    description: str = ""
    """What the property means."""
    orderable: bool | None = None
    """Whether the host's engine may sort or deduplicate on it."""

    def __post_init__(self) -> None:
        if not _PROPERTY_RE.match(f"{self.entity}.{self.property}"):
            raise ValueError(f"property fact {self.entity!r}.{self.property!r} is not a Class.property name")

    def line(self) -> str:
        """The fact's line in the section; ``""`` when it declares nothing."""
        parts = []
        type_ = _clean(self.type)
        if type_:
            parts.append(f"type {type_}")
        if self.required is not None:
            parts.append("always present" if self.required else "may be missing")
        if self.orderable is not None:
            parts.append(
                "may be sorted or deduplicated" if self.orderable else "may not be sorted or deduplicated"
            )
        description = _clean(self.description)
        if description:
            parts.append(description)
        return f"- {self.entity}.{self.property}: " + "; ".join(parts) if parts else ""


@dataclass(frozen=True)
class HostExtensions:
    """What the host's engine accepts beyond the target language."""

    predicates: tuple[HostPredicate, ...] = ()
    properties: tuple[PropertyFact, ...] = ()
    rules: tuple[str, ...] = ()
    """Limits the host's engine adds that are not about one property, one sentence each."""

    @property
    def empty(self) -> bool:
        """True when nothing would render."""
        return (
            not any(p.applies_to for p in self.predicates)
            and not any(f.line() for f in self.properties)
            and not any(_clean(r) for r in self.rules)
        )


def host_prompt_section(ext: HostExtensions | None) -> str:
    """The system-prompt section for *ext*; ``""`` when it declares nothing that renders.

    Blocks, each present only when it has content: the offered predicates under
    :data:`HOST_HEADER` with :data:`HOST_RULE`, then the rules under
    :data:`RULES_HEADER`, then the property facts under :data:`FACTS_HEADER`.
    """
    if ext is None or ext.empty:
        return ""
    blocks: list[str] = []
    offered = [p for p in ext.predicates if p.applies_to]
    if offered:
        lines = [HOST_HEADER, HOST_RULE]
        for pred in offered:
            lines += pred.prompt_lines()
        blocks.append("\n".join(lines))
    rules = [f"- {r}" for r in map(_clean, ext.rules) if r]
    if rules:
        blocks.append("\n".join([RULES_HEADER, *rules]))
    facts = [line for line in (f.line() for f in ext.properties) if line]
    if facts:
        blocks.append("\n".join([FACTS_HEADER, *facts]))
    return "\n\n".join(blocks)
