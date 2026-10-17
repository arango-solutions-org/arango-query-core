"""Structural vertex-centric index (VCI) detection in MappingResolver.

ArangoDB does not set a ``vci`` boolean on the persistent edge indexes that
*are* vertex-centric (e.g. ``["_from", "type", "_toType"]``). A flag-only
check therefore produced a false "no VCI" warning on real databases and also
suppressed the traversal index hint. These tests pin the structural detection
(`_is_structural_vci`) and its propagation through ``resolve_indexes`` /
``has_vci`` so the warning stays accurate and hints can be emitted.
"""

from __future__ import annotations

from arango_query_core.mapping import MappingBundle, MappingResolver, _is_structural_vci


def _bundle_with_edge_indexes(indexes: list[dict]) -> MappingBundle:
    return MappingBundle(
        conceptual_schema={},
        physical_mapping={
            "relationships": {
                "REL": {
                    "style": "GENERIC_WITH_TYPE",
                    "edgeCollectionName": "edges",
                    "typeField": "type",
                    "indexes": indexes,
                }
            }
        },
        metadata={},
    )


class TestIsStructuralVci:
    def test_explicit_vci_type(self) -> None:
        assert _is_structural_vci("vertex_centric_index", ("anything",)) is True
        assert _is_structural_vci("vci", ("x",)) is True

    def test_from_plus_property_is_vci(self) -> None:
        assert _is_structural_vci("persistent", ("_from", "type", "_toType")) is True

    def test_to_plus_property_is_vci(self) -> None:
        assert _is_structural_vci("persistent", ("_to", "type", "_fromType")) is True

    def test_bare_edge_index_is_not_vci(self) -> None:
        # _from/_to only, no discriminator property → cannot filter by type.
        assert _is_structural_vci("edge", ("_from", "_to")) is False

    def test_property_only_index_is_not_vci(self) -> None:
        assert _is_structural_vci("persistent", ("ticker", "year")) is False

    def test_non_indexable_type_is_not_vci(self) -> None:
        assert _is_structural_vci("fulltext", ("_from", "text")) is False

    def test_empty_fields_is_not_vci(self) -> None:
        assert _is_structural_vci("persistent", ()) is False


class TestResolverStructuralVci:
    def test_persistent_from_type_index_marks_vci(self) -> None:
        b = _bundle_with_edge_indexes(
            [{"type": "persistent", "fields": ["_from", "type", "_toType"], "name": "vci_ft"}]
        )
        r = MappingResolver(b)
        assert r.has_vci("REL") is True
        idx = r.resolve_indexes("REL")[0]
        assert idx.vci is True
        assert idx.name == "vci_ft"

    def test_explicit_flag_still_honored(self) -> None:
        # Pre-existing fixtures rely on an explicit ``vci: true`` on a
        # property-named index; that must keep working (no regression).
        b = _bundle_with_edge_indexes(
            [{"type": "persistent", "fields": ["relation"], "name": "idx_rel", "vci": True}]
        )
        assert MappingResolver(b).has_vci("REL") is True

    def test_no_vci_when_only_bare_edge_and_property_indexes(self) -> None:
        b = _bundle_with_edge_indexes(
            [
                {"type": "edge", "fields": ["_from", "_to"], "name": "edge"},
                {"type": "persistent", "fields": ["ticker", "year"], "name": "rel_ty"},
            ]
        )
        assert MappingResolver(b).has_vci("REL") is False

    def test_no_indexes_means_no_vci(self) -> None:
        b = _bundle_with_edge_indexes([])
        assert MappingResolver(b).has_vci("REL") is False


class TestArrayExpandedIndexIsNotTraversalSafe:
    """An edge index with an array-expanded field must never be hinted in a traversal.

    A persistent index stores one entry per array element and a traversal scans
    every entry under the _from/_to prefix, so ``[_from, type, toLabels[*]]``
    returns each edge once per label (measured on ArangoDB 3.12.9: 20 labels
    per edge -> 20x the rows at one hop, 400x at two). r2g provisions this shape
    when ``LpgLayout.index_edge_labels`` is on.
    """

    def test_array_expanded_index_is_not_vci(self) -> None:
        b = _bundle_with_edge_indexes(
            [{"type": "persistent", "fields": ["_from", "type", "toLabels[*]"], "name": "lbl"}]
        )
        r = MappingResolver(b)
        (idx,) = r.resolve_indexes("REL")
        assert idx.traversal_safe is False
        assert idx.vci is False
        assert r.has_vci("REL") is False

    def test_explicit_flag_does_not_override_array_expansion(self) -> None:
        b = _bundle_with_edge_indexes(
            [{"type": "persistent", "fields": ["_from", "toLabels[*]"], "name": "lbl", "vci": True}]
        )
        assert MappingResolver(b).has_vci("REL") is False

    def test_safe_index_still_vci_alongside_risky_one(self) -> None:
        b = _bundle_with_edge_indexes(
            [
                {"type": "persistent", "fields": ["_from", "type"], "name": "flat"},
                {"type": "persistent", "fields": ["_from", "type", "toLabels[*]"], "name": "lbl"},
            ]
        )
        by_name = {i.name: i for i in MappingResolver(b).resolve_indexes("REL")}
        assert by_name["flat"].vci is True and by_name["flat"].traversal_safe is True
        assert by_name["lbl"].vci is False

    def test_analyzer_verdict_is_honored(self) -> None:
        # The schema analyzer's traversalSafe: false wins even for a shape the
        # local check would accept -- its verdict is the source of truth.
        b = _bundle_with_edge_indexes([{"type": "persistent", "fields": ["_from", "type"], "name": "flat"}])
        b.physical_mapping["relationships"]["REL"]["vci"] = {
            "indexLevel": {"indexes": [{"fields": ["_from", "type"], "traversalSafe": False}]}
        }
        (idx,) = MappingResolver(b).resolve_indexes("REL")
        assert idx.traversal_safe is False and idx.vci is False

    def test_analyzer_block_without_verdict_keeps_safe_index(self) -> None:
        # Exports before analyzer 0.15.2 have a vci block but no traversalSafe:
        # absence is "no opinion", not "unsafe".
        b = _bundle_with_edge_indexes([{"type": "persistent", "fields": ["_from", "type"], "name": "flat"}])
        b.physical_mapping["relationships"]["REL"]["vci"] = {
            "indexLevel": {"indexes": [{"fields": ["_from", "type"], "discriminatorFields": ["type"]}]}
        }
        (idx,) = MappingResolver(b).resolve_indexes("REL")
        assert idx.traversal_safe is True and idx.vci is True
