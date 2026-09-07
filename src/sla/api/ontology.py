"""Serve the ontology to the browser.

The canvas builds its stylesheet — icons, colours, edge arrows — and its
right-click menu from this, rather than from a generated JavaScript file. One
fewer artefact that can fall behind the ontology, and editing the YAML is
reflected on reload.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter

from sla.ontology import Ontology, load

router = APIRouter(prefix="/api", tags=["ontology"])


@lru_cache
def _ontology() -> Ontology:
    """Load once per process. Restart to pick up an edited ontology file."""
    return load()


@router.get("/ontology")
def get_ontology() -> dict[str, Any]:
    """Return the ontology in the shape the canvas needs."""
    ontology = _ontology()
    return {
        "version": ontology.version,
        "name": ontology.name,
        "entity_types": {
            name: _entity_type(ontology, name) for name in sorted(ontology.concrete_entity_types)
        },
        "relationship_types": {
            name: _relationship_type(ontology, name, system=False)
            for name in sorted(ontology.relationship_types)
        },
        "system_relationship_types": {
            name: _relationship_type(ontology, name, system=True)
            for name in sorted(ontology.system_relationship_types)
        },
        # Types the canvas hides unless sources are explicitly requested.
        "source_types": list(ontology.concrete_subtypes("Source")),
    }


def _entity_type(ontology: Ontology, name: str) -> dict[str, Any]:
    resolved = ontology.entity_type(name)
    return {
        "label": resolved.spec.label,
        "plural": resolved.spec.plural or f"{resolved.spec.label}s",
        "description": " ".join((resolved.spec.description or "").split()),
        "icon": resolved.spec.icon,
        "color": resolved.spec.color,
        "display_name": resolved.spec.display_name,
        "labels": list(resolved.labels),
        "properties": {
            prop_name: {
                "type": prop.type.value,
                "label": prop_name.replace("_", " ").capitalize(),
                "description": " ".join((prop.description or "").split()),
                "required": prop.required,
                "multi": prop.multi,
                "enum": prop.enum,
                "pii": prop.pii,
            }
            for prop_name, prop in sorted(resolved.properties.items())
        },
        # Drives the right-click menu: only relationships this type can start.
        "outgoing_relationships": list(ontology.relationships_from(name)),
    }


def _relationship_type(ontology: Ontology, name: str, *, system: bool) -> dict[str, Any]:
    rel = ontology.relationship_type(name)
    return {
        "label": rel.label,
        "description": " ".join((rel.description or "").split()),
        "directed": rel.directed,
        "symmetric": rel.symmetric,
        "temporal": rel.temporal,
        "system": system,
        "source_types": sorted(
            {t for declared in rel.source for t in ontology.concrete_subtypes(declared)}
        ),
        "target_types": sorted(
            {t for declared in rel.target for t in ontology.concrete_subtypes(declared)}
        ),
        "properties": {
            prop_name: {
                "type": prop.type.value,
                "label": prop_name.replace("_", " ").capitalize(),
                "required": prop.required,
                "multi": prop.multi,
                "enum": prop.enum,
            }
            for prop_name, prop in sorted(rel.properties.items())
        },
    }
