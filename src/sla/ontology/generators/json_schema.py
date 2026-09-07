"""Generate a JSON Schema description of the ontology.

Two consumers: other tools that want the data model without reading YAML, and
language-model structured output. Handing a model the schema for ``Company``
means extraction is ontology-conformant by construction rather than by asking
politely in a prompt.
"""

from __future__ import annotations

import json
from typing import Any

from sla.ontology.generators._common import BANNER, JSON_TYPES
from sla.ontology.loader import Ontology
from sla.ontology.spec import PropertySpec, RelationshipTypeSpec

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def render(ontology: Ontology) -> str:
    """Render the schema document, with stable key ordering."""
    document: dict[str, Any] = {
        "$schema": SCHEMA_DIALECT,
        "$id": f"https://outerrelay.github.io/simple-link-analysis/ontology/{ontology.version}",
        "title": ontology.name,
        "description": " ".join(ontology.spec.description.split()),
        "x-generated-by": BANNER.replace("\n", " "),
        "x-ontology-version": ontology.version,
        "$defs": {
            **{
                name: _entity_schema(ontology, name)
                for name in sorted(ontology.concrete_entity_types)
            },
            **{
                name: _relationship_schema(ontology, name, rel)
                for name, rel in sorted(ontology.all_relationship_types.items())
            },
        },
        "properties": {
            "entities": {
                "type": "array",
                "description": "Entities conforming to the ontology.",
                "items": {
                    "oneOf": [
                        {"$ref": f"#/$defs/{name}"}
                        for name in sorted(ontology.concrete_entity_types)
                    ]
                },
            },
            "relationships": {
                "type": "array",
                "description": "Relationships between the entities above.",
                "items": {
                    "oneOf": [
                        {"$ref": f"#/$defs/{name}"}
                        for name in sorted(ontology.all_relationship_types)
                    ]
                },
            },
        },
        "type": "object",
    }
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _entity_schema(ontology: Ontology, name: str) -> dict[str, Any]:
    resolved = ontology.entity_type(name)
    properties: dict[str, Any] = {
        "type": {"const": name, "description": "Entity type discriminator."}
    }
    required = ["type"]

    for prop_name, prop in resolved.properties.items():
        properties[prop_name] = _property_schema(prop)
        if prop.required:
            required.append(prop_name)

    schema: dict[str, Any] = {
        "type": "object",
        "title": resolved.spec.label,
        "description": " ".join((resolved.spec.description or "").split()),
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if resolved.ancestors:
        schema["x-extends"] = list(resolved.ancestors)
    if resolved.spec.ftm:
        schema["x-followthemoney"] = resolved.spec.ftm
    if resolved.spec.identifiers:
        schema["x-identifiers"] = list(resolved.spec.identifiers)
    return schema


def _relationship_schema(
    ontology: Ontology, name: str, rel: RelationshipTypeSpec
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "type": {"const": name, "description": "Relationship type discriminator."},
        "source_id": {"type": "string", "description": "UUID of the source entity."},
        "target_id": {"type": "string", "description": "UUID of the target entity."},
    }
    required = ["type", "source_id", "target_id"]

    if rel.temporal:
        properties["valid_from"] = {
            "type": ["string", "null"],
            "format": "date",
            "description": "When this began to hold in the world.",
        }
        properties["valid_to"] = {
            "type": ["string", "null"],
            "format": "date",
            "description": "When it ceased to hold, if it has.",
        }

    for prop_name, prop in rel.properties.items():
        properties[prop_name] = _property_schema(prop)
        if prop.required:
            required.append(prop_name)

    schema: dict[str, Any] = {
        "type": "object",
        "title": rel.label,
        "description": " ".join((rel.description or "").split()),
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "x-directed": rel.directed,
        # Endpoints are expanded to concrete types so a consumer never has to
        # resolve the ontology's inheritance itself.
        "x-source-types": sorted(
            {t for declared in rel.source for t in ontology.concrete_subtypes(declared)}
        ),
        "x-target-types": sorted(
            {t for declared in rel.target for t in ontology.concrete_subtypes(declared)}
        ),
    }
    if rel.ftm:
        schema["x-followthemoney"] = rel.ftm
    return schema


def _property_schema(prop: PropertySpec) -> dict[str, Any]:
    json_type, json_format = JSON_TYPES[prop.type]

    inner: dict[str, Any] = {"type": json_type}
    if json_format:
        inner["format"] = json_format
    if prop.enum:
        inner["enum"] = list(prop.enum)

    if prop.multi:
        schema: dict[str, Any] = {"type": "array", "items": inner}
    elif prop.required:
        schema = dict(inner)
    else:
        # Nullable rather than merely absent, so a model can say "not stated".
        schema = dict(inner)
        schema["type"] = [json_type, "null"]

    if prop.description:
        schema["description"] = " ".join(prop.description.split())
    if prop.pii:
        schema["x-pii"] = True
    return schema
