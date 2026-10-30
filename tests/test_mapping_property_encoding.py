"""Temporal property encodings: ``PropertyInfo.encoding``."""

from __future__ import annotations

import pytest

from arango_query_core import TEMPORAL_ENCODINGS, CoreError, MappingBundle, MappingResolver


def _resolver(properties: dict, rel_properties: dict | None = None) -> MappingResolver:
    return MappingResolver(
        MappingBundle(
            conceptual_schema={"entities": [], "relationships": []},
            physical_mapping={
                "entities": {
                    "Post": {"collectionName": "post", "style": "COLLECTION", "properties": properties},
                    "Person": {"collectionName": "person", "style": "COLLECTION", "properties": {}},
                },
                "relationships": {
                    "KNOWS": {
                        "edgeCollectionName": "knows",
                        "style": "DEDICATED_COLLECTION",
                        "domain": "Person",
                        "range": "Person",
                        "properties": rel_properties or {},
                    }
                },
            },
            metadata={},
        )
    )


def test_the_vocabulary() -> None:
    assert frozenset({"epoch_ms", "iso8601"}) == TEMPORAL_ENCODINGS


def test_a_declared_encoding_is_read_for_entities_and_relationships() -> None:
    resolver = _resolver(
        {"creationDate": {"type": "datetime", "encoding": "epoch_ms"}},
        {"creationDate": {"type": "datetime", "encoding": "iso8601"}},
    )
    assert resolver.resolve_properties("Post")["creationDate"].encoding == "epoch_ms"
    assert resolver.resolve_properties("KNOWS")["creationDate"].encoding == "iso8601"


def test_no_declaration_means_unknown() -> None:
    props = _resolver({"creationDate": {"type": "datetime"}, "title": "string", "id": None})
    infos = props.resolve_properties("Post")
    assert {name: info.encoding for name, info in infos.items()} == {
        "creationDate": "",
        "title": "",
        "id": "",
    }


@pytest.mark.parametrize("bad", ["epochMillis", "ISO8601", "seconds", 13])
def test_an_unknown_encoding_is_an_invalid_mapping(bad: object) -> None:
    resolver = _resolver({"creationDate": {"type": "datetime", "encoding": bad}})
    with pytest.raises(CoreError, match="Post.creationDate declares encoding") as err:
        resolver.resolve_properties("Post")
    assert err.value.code == "INVALID_MAPPING"


def test_the_schema_summary_carries_the_encoding_when_declared() -> None:
    summary = _resolver(
        {"creationDate": {"type": "datetime", "encoding": "iso8601"}, "title": {"type": "string"}},
        {"creationDate": {"type": "datetime", "encoding": "epoch_ms"}},
    ).schema_summary()
    post = next(e for e in summary["entities"] if e["label"] == "Post")
    assert post["properties"]["creationDate"]["encoding"] == "iso8601"
    assert "encoding" not in post["properties"]["title"]
    knows = next(r for r in summary["relationships"] if r["type"] == "KNOWS")
    assert knows["properties"]["creationDate"]["encoding"] == "epoch_ms"
