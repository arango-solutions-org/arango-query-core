"""The ``LABELS`` entity style and the ``*`` catch-all key.

``LABELS`` keeps a node's labels in an array field of a shared collection, so a
node can carry several labels (Neo4j-shaped data). ``*`` under ``entities`` or
``relationships`` resolves any label or type without an entry of its own.
Bundles without either behave exactly as before.
"""

from __future__ import annotations

import pytest

from arango_query_core import CoreError
from arango_query_core.mapping import CATCH_ALL_KEY, MappingBundle, MappingResolver
from arango_query_core.owl_turtle import mapping_to_turtle, turtle_to_mapping


def _bundle() -> MappingBundle:
    return MappingBundle(
        conceptual_schema={"entities": [{"name": "Person", "labels": ["Person"], "properties": []}]},
        physical_mapping={
            "entities": {
                "Person": {"style": "LABELS", "collectionName": "nodes", "labelsField": "labels"},
                CATCH_ALL_KEY: {"style": "LABELS", "collectionName": "nodes", "labelsField": "labels"},
            },
            "relationships": {
                CATCH_ALL_KEY: {
                    "style": "GENERIC_WITH_TYPE",
                    "edgeCollectionName": "edges",
                    "typeField": "type",
                },
            },
        },
        metadata={},
    )


def test_labels_entity_resolves_unchanged() -> None:
    r = MappingResolver(_bundle())
    assert r.resolve_entity("Person") == {
        "style": "LABELS",
        "collectionName": "nodes",
        "labelsField": "labels",
    }


def test_catch_all_after_exact_and_normalized() -> None:
    r = MappingResolver(_bundle())
    assert r.resolve_entity("person")["collectionName"] == "nodes"  # normalized hit on Person
    assert r.resolve_entity("Actor") == _bundle_entity(r, CATCH_ALL_KEY)
    assert r.resolve_relationship("ACTED_IN")["edgeCollectionName"] == "edges"


def _bundle_entity(r: MappingResolver, key: str) -> object:
    return r.bundle.physical_mapping["entities"][key]


def test_catch_all_is_not_listed_as_a_label() -> None:
    r = MappingResolver(_bundle())
    assert r.all_entity_labels() == ["Person"]
    assert r.all_relationship_types() == []
    summary = r.schema_summary()
    assert [e["label"] for e in summary["entities"]] == ["Person"]
    assert summary["entities"][0]["labelsField"] == "labels"


def test_no_catch_all_still_raises() -> None:
    b = _bundle()
    del b.physical_mapping["entities"][CATCH_ALL_KEY]
    with pytest.raises(CoreError):
        MappingResolver(b).resolve_entity("Actor")


def test_labels_field_round_trips_through_turtle() -> None:
    ttl = mapping_to_turtle(_bundle())
    assert 'phys:labelsField "labels"' in ttl
    back = turtle_to_mapping(ttl)
    assert back.physical_mapping["entities"]["Person"]["labelsField"] == "labels"
