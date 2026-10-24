"""OWL Turtle serialization and deserialization for MappingBundle.

Supports round-tripping between the internal MappingBundle format and
an OWL/Turtle ontology representation, following the ArangoDB annotation
convention using ``phys:`` annotations for physical mapping info.
"""

from __future__ import annotations

import re
from typing import Any

from .mapping import MappingBundle, MappingSource

_PREFIXES = """\
@prefix : <http://arangodb.com/schema/hybrid#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix phys: <http://arangodb.com/schema/physical#> .
"""


def mapping_to_turtle(bundle: MappingBundle) -> str:
    """Serialize a MappingBundle to OWL/Turtle format."""
    lines = [_PREFIXES.strip(), ""]

    lines.append(": a owl:Ontology ;")
    lines.append('  rdfs:label "Conceptual Schema" ;')
    lines.append('  rdfs:comment "Conceptual schema from ArangoDB mapping." .')
    lines.append("")

    lines.append("phys:mappingStyle a owl:AnnotationProperty .")
    lines.append("phys:collectionName a owl:AnnotationProperty .")
    lines.append("phys:typeField a owl:AnnotationProperty .")
    lines.append("phys:typeValue a owl:AnnotationProperty .")
    lines.append("phys:labelsField a owl:AnnotationProperty .")
    lines.append("phys:edgeCollectionName a owl:AnnotationProperty .")
    lines.append("")

    cs = bundle.conceptual_schema
    pm = bundle.physical_mapping
    entities = pm.get("entities", {})
    relationships = pm.get("relationships", {})

    for entity_def in cs.get("entities", []):
        name = entity_def.get("name", "")
        if not name:
            continue
        lines.append(f":{name} a owl:Class ;")
        lines.append(f'  rdfs:label "{name}" .')
        emap = entities.get(name, {})
        style = emap.get("style", "COLLECTION")
        lines.append(f':{name} phys:mappingStyle "{style}" .')
        coll = emap.get("collectionName", "")
        if coll:
            lines.append(f':{name} phys:collectionName "{coll}" .')
        if style == "LABEL":
            tf = emap.get("typeField", "")
            tv = emap.get("typeValue", "")
            if tf:
                lines.append(f':{name} phys:typeField "{tf}" .')
            if tv:
                lines.append(f':{name} phys:typeValue "{tv}" .')
        elif style == "LABELS" and emap.get("labelsField"):
            lines.append(f':{name} phys:labelsField "{emap["labelsField"]}" .')

        for prop in entity_def.get("properties", []):
            pname = prop.get("name", "")
            ptype = prop.get("type", "string")
            xsd = _to_xsd_type(ptype)
            lines.append(f":{pname} a owl:DatatypeProperty ;")
            lines.append(f"  rdfs:domain :{name} ;")
            lines.append(f"  rdfs:range {xsd} .")

        lines.append("")

    for rel_def in cs.get("relationships", []):
        rtype = rel_def.get("type", "")
        if not rtype:
            continue
        from_e = rel_def.get("fromEntity", "Any")
        to_e = rel_def.get("toEntity", "Any")
        lines.append(f":{rtype} a owl:ObjectProperty ;")
        lines.append(f'  rdfs:label "{rtype}" ;')
        lines.append(f"  rdfs:domain :{from_e} ;")
        lines.append(f"  rdfs:range :{to_e}  .")
        rmap = relationships.get(rtype, {})
        rstyle = rmap.get("style", "DEDICATED_COLLECTION")
        lines.append(f':{rtype} phys:mappingStyle "{rstyle}" .')
        ecoll = rmap.get("edgeCollectionName", "")
        if ecoll:
            lines.append(f':{rtype} phys:edgeCollectionName "{ecoll}" .')
        if rstyle == "GENERIC_WITH_TYPE":
            tf = rmap.get("typeField", "")
            tv = rmap.get("typeValue", "")
            if tf:
                lines.append(f':{rtype} phys:typeField "{tf}" .')
            if tv:
                lines.append(f':{rtype} phys:typeValue "{tv}" .')

        for prop in rel_def.get("properties", []):
            pname = prop.get("name", "")
            ptype = prop.get("type", "string")
            xsd = _to_xsd_type(ptype)
            lines.append(f":{rtype}_{pname} a owl:DatatypeProperty ;")
            lines.append(f"  rdfs:domain :{rtype} ;")
            lines.append(f"  rdfs:range {xsd} .")

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def turtle_to_mapping(turtle: str) -> MappingBundle:
    """Parse an OWL/Turtle string into a MappingBundle.

    A line-oriented reader for the layout :func:`mapping_to_turtle` and
    arangodb-schema-analyzer write (no rdflib needed; see
    :mod:`.owl_rdflib` for arbitrary Turtle). A datatype property keeps its
    full local name (``:file_name`` is ``file_name``); only a relationship
    property written ``:<REL>_<prop>`` with ``rdfs:domain :<REL>`` loses the
    ``<REL>_`` prefix. Its domain may be one class, several ``rdfs:domain``
    lines, or an ``owl:unionOf`` list (the analyzer's form for a field
    several classes share), and ``phys:attributeName`` names the stored
    field when it differs from the property name.
    """
    classes: dict[str, dict[str, str]] = {}
    obj_props: dict[str, dict[str, str]] = {}
    data_props: list[dict[str, Any]] = []
    annotations: dict[str, dict[str, str]] = {}

    current_subject: str | None = None
    current_kind: str | None = None  # "class", "objprop", "dataprop"

    for line in turtle.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("@prefix") or stripped.startswith("#"):
            continue

        m_class = re.match(r":(\w+)\s+a\s+owl:Class", stripped)
        if m_class:
            current_subject = m_class.group(1)
            current_kind = "class"
            classes.setdefault(current_subject, {})
            continue

        m_objprop = re.match(r":(\w+)\s+a\s+owl:ObjectProperty", stripped)
        if m_objprop:
            current_subject = m_objprop.group(1)
            current_kind = "objprop"
            obj_props.setdefault(current_subject, {})
            continue

        m_dataprop = re.match(r":(\w+)\s+a\s+owl:DatatypeProperty", stripped)
        if m_dataprop:
            current_subject = m_dataprop.group(1)
            current_kind = "dataprop"
            data_props.append({"name": current_subject, "domains": []})
            continue

        m_union = re.match(r"rdfs:domain\s+\[.*owl:unionOf\s*\(([^)]*)\)", stripped)
        if m_union:
            if current_kind == "dataprop" and data_props:
                data_props[-1]["domains"].extend(re.findall(r":(\w+)", m_union.group(1)))
            continue

        m_domain = re.match(r"rdfs:domain\s+:(\w+)", stripped)
        if m_domain:
            domain = m_domain.group(1)
            if current_kind == "dataprop" and data_props:
                data_props[-1]["domains"].append(domain)
            elif current_kind == "objprop" and current_subject:
                obj_props[current_subject]["domain"] = domain
            continue

        m_range = re.match(r"rdfs:range\s+:?(\S+)", stripped)
        if m_range:
            rng = m_range.group(1).rstrip(" .;")
            if current_kind == "dataprop" and data_props:
                data_props[-1]["range"] = rng
            elif current_kind == "objprop" and current_subject:
                obj_props[current_subject]["range"] = rng.lstrip(":")
            continue

        for annot in (
            "mappingStyle",
            "collectionName",
            "typeField",
            "typeValue",
            "labelsField",
            "edgeCollectionName",
            "attributeName",
        ):
            m_a = re.match(rf':(\w+)\s+phys:{annot}\s+"([^"]+)"', stripped)
            if m_a:
                subject = m_a.group(1)
                val = m_a.group(2)
                annotations.setdefault(subject, {})[annot] = val

    entities_cs: list[dict[str, Any]] = []
    entities_pm: dict[str, dict[str, Any]] = {}
    for name in classes:
        own = [dp for dp in data_props if name in dp["domains"]]
        props = [{"name": dp["name"], "type": _from_xsd_type(dp.get("range", "xsd:string"))} for dp in own]
        entities_cs.append({"name": name, "labels": [name], "properties": props})
        ann = annotations.get(name, {})
        pm_entry: dict[str, Any] = {
            "style": ann.get("mappingStyle", "COLLECTION"),
            "collectionName": ann.get("collectionName", name.lower() + "s"),
        }
        if pm_entry["style"] == "LABEL":
            pm_entry["typeField"] = ann.get("typeField", "type")
            pm_entry["typeValue"] = ann.get("typeValue", name)
        elif pm_entry["style"] == "LABELS":
            pm_entry["labelsField"] = ann.get("labelsField", "labels")
        fields = {
            dp["name"]: {"field": annotations[dp["name"]]["attributeName"]}
            for dp in own
            if annotations.get(dp["name"], {}).get("attributeName")
        }
        if fields:
            pm_entry["properties"] = fields
        entities_pm[name] = pm_entry

    rels_cs: list[dict[str, Any]] = []
    rels_pm: dict[str, dict[str, Any]] = {}
    for rtype in obj_props:
        op = obj_props[rtype]
        from_e = op.get("domain", "Any")
        to_e = op.get("range", "Any")
        props = [
            {
                # Exactly the `<REL>_` prefix mapping_to_turtle adds, not every occurrence.
                "name": dp["name"].removeprefix(f"{rtype}_"),
                "type": _from_xsd_type(dp.get("range", "xsd:string")),
            }
            for dp in data_props
            if rtype in dp["domains"]
        ]
        rels_cs.append({"type": rtype, "fromEntity": from_e, "toEntity": to_e, "properties": props})
        ann = annotations.get(rtype, {})
        pm_entry = {
            "style": ann.get("mappingStyle", "DEDICATED_COLLECTION"),
            "edgeCollectionName": ann.get("edgeCollectionName", rtype.lower()),
        }
        if pm_entry["style"] == "GENERIC_WITH_TYPE":
            pm_entry["typeField"] = ann.get("typeField", "type")
            pm_entry["typeValue"] = ann.get("typeValue", rtype)
        rels_pm[rtype] = pm_entry

    return MappingBundle(
        conceptual_schema={"entities": entities_cs, "relationships": rels_cs},
        physical_mapping={"entities": entities_pm, "relationships": rels_pm},
        metadata={"provider": "owl_turtle_import"},
        source=MappingSource(kind="owl_turtle"),
    )


def _to_xsd_type(t: str) -> str:
    return {
        "string": "xsd:string",
        "integer": "xsd:integer",
        "int": "xsd:integer",
        "number": "xsd:decimal",
        "float": "xsd:decimal",
        "double": "xsd:double",
        "boolean": "xsd:boolean",
        "date": "xsd:date",
        "datetime": "xsd:dateTime",
    }.get(t.lower(), "xsd:string")


def _from_xsd_type(t: str) -> str:
    return {
        "xsd:string": "string",
        "xsd:integer": "integer",
        "xsd:decimal": "number",
        "xsd:double": "number",
        "xsd:boolean": "boolean",
        "xsd:date": "date",
        "xsd:dateTime": "datetime",
    }.get(t, "string")
