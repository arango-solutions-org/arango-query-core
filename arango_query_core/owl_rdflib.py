"""OWL Turtle ingestion via rdflib — for importing external ontologies.

Unlike :mod:`owl_turtle` (which round-trips our own annotation convention),
this module uses ``rdflib`` for robust parsing of arbitrary OWL/Turtle files
produced by Protégé, TopBraid, or any standards-compliant OWL toolchain.
"""

from __future__ import annotations

from typing import Any

from .mapping import MappingBundle, MappingSource


def parse_owl_with_rdflib(turtle_text: str) -> MappingBundle:
    """Parse an OWL/Turtle ontology using rdflib and produce a MappingBundle.

    Handles:
    - ``owl:Class`` → entity in conceptual schema
    - ``owl:ObjectProperty`` → relationship in conceptual schema
    - ``rdfs:domain`` / ``rdfs:range`` → fromEntity / toEntity
    - ``owl:DatatypeProperty`` → property on the domain entity
    - ``rdfs:label`` → human-readable name
    - Custom ``phys:`` annotations → physical mapping (if present)
    """
    try:
        import rdflib
    except ImportError as e:
        raise ImportError(
            "rdflib is required for OWL ingestion. Install with: pip install arango-cypher-py[owl]"
        ) from e

    g = rdflib.Graph()
    g.parse(data=turtle_text, format="turtle")

    OWL = rdflib.Namespace("http://www.w3.org/2002/07/owl#")
    RDFS = rdflib.Namespace("http://www.w3.org/2000/01/rdf-schema#")
    RDF = rdflib.Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    PHYS = rdflib.Namespace("http://arangodb.com/schema/physical#")

    # ── Extract classes ──────────────────────────────────────────────
    entities: list[dict[str, Any]] = []
    entity_props: dict[str, list[dict[str, str]]] = {}

    for cls in _named_classes(g, RDF, OWL):
        name = _local_name(cls)
        entities.append({"name": name, "labels": [name], "properties": []})
        entity_props[name] = []

    # ── Extract datatype properties ──────────────────────────────────
    # A property's domain may be one class, several (each gets the field),
    # or an owl:unionOf class expression — arangodb-schema-analyzer's form
    # for a field several classes share. phys:attributeName names the
    # stored field when it differs from the property name.
    fields: dict[str, dict[str, str]] = {}
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        prop_name = _local_name(prop)
        range_uri = g.value(prop, RDFS.range)
        prop_entry: dict[str, str] = {"name": prop_name}
        if range_uri:
            prop_entry["type"] = _xsd_to_simple(str(range_uri))
        attribute = g.value(prop, PHYS.attributeName)
        for domain_node in g.objects(prop, RDFS.domain):
            for domain_name in _domain_names(g, domain_node, OWL):
                if domain_name in entity_props:
                    entity_props[domain_name].append(prop_entry)
                    if attribute:
                        fields.setdefault(domain_name, {})[prop_name] = str(attribute)

    for entity in entities:
        entity["properties"] = entity_props.get(entity["name"], [])

    # ── Extract object properties (relationships) ────────────────────
    relationships: list[dict[str, Any]] = []
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        rel_name = _local_name(prop)
        domain = g.value(prop, RDFS.domain)
        range_ = g.value(prop, RDFS.range)
        relationships.append(
            {
                "type": rel_name,
                "fromEntity": _local_name(domain) if domain else "Any",
                "toEntity": _local_name(range_) if range_ else "Any",
                "properties": [],
            }
        )

    # ── Physical mapping from phys: annotations ──────────────────────
    physical_entities: dict[str, dict[str, Any]] = {}
    physical_rels: dict[str, dict[str, Any]] = {}

    for cls in _named_classes(g, RDF, OWL):
        name = _local_name(cls)
        coll = g.value(cls, PHYS.collectionName)
        style = g.value(cls, PHYS.style) or g.value(cls, PHYS.mappingStyle)
        if coll:
            pm_entry: dict[str, Any] = {
                "style": str(style) if style else "COLLECTION",
                "collectionName": str(coll),
                "properties": {},
            }
            type_field = g.value(cls, PHYS.typeField)
            type_value = g.value(cls, PHYS.typeValue)
            if type_field:
                pm_entry["typeField"] = str(type_field)
            if type_value:
                pm_entry["typeValue"] = str(type_value)
            labels_field = g.value(cls, PHYS.labelsField)
            if labels_field:
                pm_entry["labelsField"] = str(labels_field)
            for prop_name, field in fields.get(name, {}).items():
                pm_entry["properties"][prop_name] = {"field": field}
            physical_entities[name] = pm_entry

    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        rel_name = _local_name(prop)
        edge_coll = g.value(prop, PHYS.edgeCollectionName) or g.value(prop, PHYS.collectionName)
        style = g.value(prop, PHYS.style) or g.value(prop, PHYS.mappingStyle)
        if edge_coll:
            pm_entry = {
                "style": str(style) if style else "DEDICATED_COLLECTION",
                "edgeCollectionName": str(edge_coll),
                "properties": {},
            }
            domain = g.value(prop, RDFS.domain)
            range_ = g.value(prop, RDFS.range)
            if domain:
                pm_entry["domain"] = _local_name(domain)
            if range_:
                pm_entry["range"] = _local_name(range_)
            physical_rels[rel_name] = pm_entry

    return MappingBundle(
        conceptual_schema={"entities": entities, "relationships": relationships},
        physical_mapping={"entities": physical_entities, "relationships": physical_rels},
        metadata={"source": "owl_rdflib"},
        owl_turtle=turtle_text,
        source=MappingSource(kind="owl_turtle"),
    )


def _named_classes(g: Any, rdf: Any, owl: Any) -> list[Any]:
    """Every ``owl:Class`` with an IRI. An anonymous class expression (the
    ``[ a owl:Class ; owl:unionOf ( … ) ]`` of a shared field's domain) is not
    an entity."""
    import rdflib

    return [c for c in g.subjects(rdf.type, owl.Class) if isinstance(c, rdflib.URIRef)]


def _domain_names(g: Any, domain: Any, owl: Any) -> list[str]:
    """Class names a ``rdfs:domain`` value covers: the class itself, or every
    member of an ``owl:unionOf`` list."""
    import rdflib
    from rdflib.collection import Collection

    if isinstance(domain, rdflib.BNode):
        members = g.value(domain, owl.unionOf)
        return [_local_name(m) for m in Collection(g, members)] if members is not None else []
    return [_local_name(domain)]


def _local_name(uri: Any) -> str:
    """Extract the local (fragment / last-path-segment) name from a URI."""
    s = str(uri)
    if "#" in s:
        return s.rsplit("#", 1)[1]
    if "/" in s:
        return s.rsplit("/", 1)[1]
    return s


def _xsd_to_simple(uri: str) -> str:
    """Map an XSD datatype URI to a simple type string."""
    mapping = {
        "http://www.w3.org/2001/XMLSchema#string": "string",
        "http://www.w3.org/2001/XMLSchema#integer": "integer",
        "http://www.w3.org/2001/XMLSchema#int": "integer",
        "http://www.w3.org/2001/XMLSchema#decimal": "number",
        "http://www.w3.org/2001/XMLSchema#double": "number",
        "http://www.w3.org/2001/XMLSchema#float": "number",
        "http://www.w3.org/2001/XMLSchema#boolean": "boolean",
        "http://www.w3.org/2001/XMLSchema#date": "date",
        "http://www.w3.org/2001/XMLSchema#dateTime": "datetime",
    }
    return mapping.get(uri, "string")
