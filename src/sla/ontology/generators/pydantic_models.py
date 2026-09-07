"""Generate Pydantic models for every concrete entity and relationship type.

These exist for editor autocompletion, type checking and request validation.
They are a convenience derived from the ontology, never a second source of
truth: the loaded ontology remains the authority at runtime.
"""

from __future__ import annotations

from sla.ontology.generators._common import BANNER, PYTHON_TYPES, docstring
from sla.ontology.loader import Ontology
from sla.ontology.spec import PropertySpec, RelationshipTypeSpec

HEADER = f'''"""{BANNER}"""

# ruff: noqa: E501
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EntityBase(BaseModel):
    """Fields every stored entity carries, supplied by the system."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="UUID assigned on creation; never a natural key.")
    observed_at: datetime | None = Field(
        default=None, description="When this entity was last confirmed by a source."
    )


class RelationshipBase(BaseModel):
    """Fields every stored relationship carries, supplied by the system."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="UUID assigned on creation.")
    source_id: str = Field(description="UUID of the entity the edge starts at.")
    target_id: str = Field(description="UUID of the entity the edge ends at.")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence in the claim."
    )
    observed_at: datetime | None = Field(
        default=None, description="When this relationship was last confirmed."
    )
    assertion_ids: list[str] = Field(
        default_factory=list,
        description="Assertions supporting this edge; it exists while one does.",
    )


class TemporalRelationshipBase(RelationshipBase):
    """A relationship that can start and stop being true."""

    valid_from: date | None = Field(
        default=None, description="When this began to hold in the world."
    )
    valid_to: date | None = Field(
        default=None, description="When it ceased to hold, if it has."
    )
'''


def render(ontology: Ontology) -> str:
    """Render the models module."""
    parts = [HEADER]

    parts.append(_section("Entities"))
    for name, resolved in sorted(ontology.concrete_entity_types.items()):
        parts.append(_entity_model(name, resolved.spec.description, resolved.properties))

    parts.append(_section("Relationships"))
    for name, rel in sorted(ontology.all_relationship_types.items()):
        parts.append(_relationship_model(name, rel))

    parts.append(_registries(ontology))
    return "".join(parts)


def _section(title: str) -> str:
    rule = "# " + "-" * 70
    return f"\n\n{rule}\n# {title}\n{rule}\n"


def _entity_model(name: str, description: str, properties: dict[str, PropertySpec]) -> str:
    lines = [f"\n\nclass {name}(EntityBase):\n"]
    if doc := docstring(description):
        lines.append(doc)
    lines.append(f'    type: Literal["{name}"] = "{name}"\n')
    lines.extend(_field(prop_name, prop) for prop_name, prop in properties.items())
    return "".join(lines)


def _relationship_model(name: str, rel: RelationshipTypeSpec) -> str:
    base = "TemporalRelationshipBase" if rel.temporal else "RelationshipBase"
    lines = [f"\n\nclass {_class_name(name)}({base}):\n"]
    if doc := docstring(rel.description or rel.label):
        lines.append(doc)
    lines.append(f'    type: Literal["{name}"] = "{name}"\n')
    lines.extend(_field(prop_name, prop) for prop_name, prop in rel.properties.items())
    return "".join(lines)


def _field(name: str, prop: PropertySpec) -> str:
    """Render one model field, preserving the ontology's description."""
    if prop.type.value == "enum" and prop.enum:
        inner = "Literal[" + ", ".join(f'"{v}"' for v in prop.enum) + "]"
    else:
        inner = PYTHON_TYPES[prop.type]

    description = " ".join((prop.description or "").split())
    args = [f"description={description!r}"] if description else []

    if prop.multi:
        annotation = f"list[{inner}]"
        args.insert(0, "default_factory=list")
    elif prop.required:
        annotation = inner
    else:
        annotation = f"{inner} | None"
        args.insert(0, "default=None")

    return f"    {name}: {annotation} = Field({', '.join(args)})\n"


def _class_name(relationship_type: str) -> str:
    """``BENEFICIAL_OWNER_OF`` -> ``BeneficialOwnerOf``."""
    return "".join(part.capitalize() for part in relationship_type.split("_"))


def _registries(ontology: Ontology) -> str:
    """Emit lookup tables so callers can go from a type name to its model."""
    entities = sorted(ontology.concrete_entity_types)
    relationships = sorted(ontology.all_relationship_types)

    entity_union = " | ".join(entities)
    relationship_union = " | ".join(_class_name(r) for r in relationships)

    entity_rows = "".join(f'    "{n}": {n},\n' for n in entities)
    relationship_rows = "".join(f'    "{n}": {_class_name(n)},\n' for n in relationships)

    return (
        f"{_section('Registries')}"
        f"\nAnyEntity = {entity_union}\n"
        f"\nAnyRelationship = {relationship_union}\n"
        "\nENTITY_MODELS: dict[str, type[EntityBase]] = {\n"
        f"{entity_rows}"
        "}\n"
        "\nRELATIONSHIP_MODELS: dict[str, type[RelationshipBase]] = {\n"
        f"{relationship_rows}"
        "}\n"
    )
