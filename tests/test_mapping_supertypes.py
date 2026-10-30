"""Supertypes (label hierarchies): the MULTI_LABEL and UNION entity styles."""

from __future__ import annotations

import pytest

from arango_query_core import SUPERTYPE_STYLES, CoreError, MappingBundle, MappingResolver


def _bundle(
    entities: dict, relationships: dict | None = None, conceptual: dict | None = None
) -> MappingBundle:
    return MappingBundle(
        conceptual_schema=conceptual or {"entities": [], "relationships": []},
        physical_mapping={"entities": entities, "relationships": relationships or {}},
        metadata={},
    )


_POST = {
    "collectionName": "post",
    "style": "COLLECTION",
    "properties": {"id": {"field": "id"}, "imageFile": {}},
}
_COMMENT = {
    "collectionName": "comment",
    "style": "COLLECTION",
    "properties": {"id": {"field": "id"}, "length": {}},
}
_HAS_CREATOR = {
    "edgeCollectionName": "has_creator",
    "style": "DEDICATED_COLLECTION",
    "domain": ["Post", "Comment"],
    "range": "Person",
}


def _pg(**extra) -> MappingResolver:
    return MappingResolver(
        _bundle(
            {
                "Post": _POST,
                "Comment": _COMMENT,
                "Person": {"collectionName": "person", "style": "COLLECTION"},
                "Message": {"style": "UNION", "members": ["Post", "Comment"]},
                **extra,
            },
            {"HAS_CREATOR": _HAS_CREATOR},
        )
    )


def test_both_styles_are_supertype_styles():
    assert {"MULTI_LABEL", "UNION"} == SUPERTYPE_STYLES


def test_union_members_and_is_supertype():
    r = _pg()
    assert r.is_supertype("Message")
    assert not r.is_supertype("Post")
    assert not r.is_supertype("Nowhere")
    assert r.supertype_members("Message") == ["Post", "Comment"]


def test_members_may_come_from_the_conceptual_subtypes():
    r = MappingResolver(
        _bundle(
            {
                "Post": {
                    "collectionName": "nodes",
                    "style": "LABEL",
                    "typeField": "type",
                    "typeValue": "Post",
                },
                "Comment": {
                    "collectionName": "nodes",
                    "style": "LABEL",
                    "typeField": "type",
                    "typeValue": "Comment",
                },
                "Message": {
                    "style": "MULTI_LABEL",
                    "collectionName": "nodes",
                    "labelsField": "labels",
                    "labelValue": "Message",
                },
            },
            conceptual={"entities": [{"name": "Message", "subtypes": ["Post", "Comment"]}]},
        )
    )
    assert r.supertype_members("Message") == ["Post", "Comment"]


def test_nested_supertypes_are_flattened_without_duplicates():
    r = _pg(
        Reply={"style": "UNION", "members": ["Comment"]},
        Content={"style": "UNION", "members": ["Message", "Reply", "Post"]},
    )
    assert r.supertype_members("Content") == ["Post", "Comment"]


@pytest.mark.parametrize(
    "extra,needle",
    [
        ({"Message": {"style": "UNION", "members": ["Post", "Ghost"]}}, "'Ghost', which is not mapped"),
        ({"Message": {"style": "UNION"}}, "declares no members"),
        (
            {
                "Message": {"style": "UNION", "members": ["Thread"]},
                "Thread": {"style": "UNION", "members": ["Message"]},
            },
            "cycle",
        ),
    ],
)
def test_invalid_hierarchies_are_refused(extra, needle):
    r = _pg(**extra)
    with pytest.raises(CoreError) as exc:
        r.supertype_members("Message")
    assert exc.value.code == "INVALID_MAPPING"
    assert needle in str(exc.value)


def test_a_concrete_label_is_not_a_supertype():
    with pytest.raises(CoreError, match="is not a supertype"):
        _pg().supertype_members("Post")


def test_supertype_without_properties_has_its_members_properties():
    props = _pg().resolve_properties("Message")
    assert set(props) == {"id", "imageFile", "length"}


def test_an_edge_whose_endpoints_are_all_members_guarantees_the_supertype():
    r = _pg()
    assert r.edge_constrains_target("HAS_CREATOR", "Message", "INBOUND")
    assert r.edge_constrains_target("HAS_CREATOR", "Person", "OUTBOUND")


def test_an_edge_that_can_reach_a_non_member_does_not():
    r = _pg(Forum={"collectionName": "forum", "style": "COLLECTION"})
    r.bundle.physical_mapping["relationships"]["HAS_TAG"] = {
        "edgeCollectionName": "has_tag",
        "style": "DEDICATED_COLLECTION",
        "domain": ["Forum", "Post", "Comment"],
        "range": "Tag",
    }
    assert not r.edge_constrains_target("HAS_TAG", "Message", "INBOUND")


def test_existing_mappings_are_unaffected():
    r = MappingResolver(_bundle({"Post": _POST}))
    assert not r.is_supertype("Post")
    assert set(r.resolve_properties("Post")) == {"id", "imageFile"}


def test_union_and_multi_label_round_trip_through_owl_as_subclasses():
    from arango_query_core import mapping_to_turtle, turtle_to_mapping

    bundle = _bundle(
        {
            "Post": {"collectionName": "post", "style": "COLLECTION"},
            "Comment": {"collectionName": "comment", "style": "COLLECTION"},
            "Message": {"style": "UNION", "members": ["Post", "Comment"]},
            "Thing": {
                "style": "MULTI_LABEL",
                "collectionName": "nodes",
                "labelsField": "labels",
                "labelValue": "Thing",
            },
        },
        conceptual={
            "entities": [
                {"name": "Post"},
                {"name": "Comment"},
                {"name": "Message"},
                {"name": "Thing", "subtypes": ["Post"]},
            ],
            "relationships": [],
        },
    )
    turtle = mapping_to_turtle(bundle)
    assert ":Post rdfs:subClassOf :Message ." in turtle
    back = turtle_to_mapping(turtle)
    pm = back.physical_mapping["entities"]
    assert pm["Message"] == {"style": "UNION", "members": ["Post", "Comment"]}
    assert pm["Thing"]["labelsField"] == "labels" and pm["Thing"]["labelValue"] == "Thing"
    r = MappingResolver(back)
    assert r.supertype_members("Message") == ["Post", "Comment"]
    assert r.supertype_members("Thing") == ["Post"]
