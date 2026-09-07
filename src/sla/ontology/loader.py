"""Load ``ontology.yaml``, validate it, and resolve its cross-references.

Parsing with :mod:`sla.ontology.spec` catches malformed declarations. This
module catches the errors that only show up once declarations are read against
each other: an ``extends`` naming a type that does not exist, a relationship
permitting an endpoint type that was deleted, an inheritance cycle, a display
template referring to a property that was renamed.

Catching these here means every generator downstream can assume a coherent
ontology and stay simple.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

from sla.ontology.spec import (
    EntityTypeSpec,
    OntologySpec,
    PropertySpec,
    RelationshipTypeSpec,
)

DEFAULT_ONTOLOGY_PATH = Path(__file__).resolve().parents[3] / "ontology" / "ontology.yaml"

# Properties every stored node carries, supplied by the system rather than the
# ontology file. Declaring one of these in the ontology is an error.
RESERVED_ENTITY_PROPERTIES = frozenset(
    {"id", "type", "created_at", "updated_at", "observed_at", "suppressed"}
)

# Likewise for relationships. Temporal relationships add valid_from/valid_to.
RESERVED_RELATIONSHIP_PROPERTIES = frozenset(
    {"id", "type", "created_at", "updated_at", "observed_at", "confidence", "assertion_ids"}
)


class OntologyError(Exception):
    """The ontology file is invalid. The message says how."""


@dataclass(frozen=True)
class ResolvedEntityType:
    """An entity type with inherited properties folded in."""

    name: str
    spec: EntityTypeSpec
    properties: dict[str, PropertySpec]
    ancestors: tuple[str, ...]
    """Supertypes, nearest first. Excludes the type itself."""

    @property
    def abstract(self) -> bool:
        return self.spec.abstract

    @property
    def labels(self) -> tuple[str, ...]:
        """Neo4j labels for a node of this type: its own, then its supertypes."""
        return (self.name, *self.ancestors)


class Ontology:
    """A validated ontology with its cross-references resolved."""

    def __init__(self, spec: OntologySpec) -> None:
        self.spec = spec
        self._entity_types = _resolve_entity_types(spec)
        _validate_references(spec, self._entity_types)

    # --- metadata ------------------------------------------------------

    @property
    def version(self) -> str:
        return self.spec.version

    @property
    def name(self) -> str:
        return self.spec.name

    # --- entity types --------------------------------------------------

    @property
    def entity_types(self) -> dict[str, ResolvedEntityType]:
        return self._entity_types

    def entity_type(self, name: str) -> ResolvedEntityType:
        try:
            return self._entity_types[name]
        except KeyError:
            raise OntologyError(f"unknown entity type {name!r}") from None

    @cached_property
    def concrete_entity_types(self) -> dict[str, ResolvedEntityType]:
        """Types that can actually be instantiated."""
        return {n: t for n, t in self._entity_types.items() if not t.abstract}

    def concrete_subtypes(self, name: str) -> tuple[str, ...]:
        """Every concrete type that ``name`` admits, itself included.

        Relationship endpoints name abstract types like ``LegalEntity``; this
        expands that to the concrete types a node may actually have.
        """
        self.entity_type(name)  # raises if unknown
        return tuple(
            sorted(
                other
                for other, resolved in self.concrete_entity_types.items()
                if other == name or name in resolved.ancestors
            )
        )

    # --- relationship types ---------------------------------------------

    @cached_property
    def relationship_types(self) -> dict[str, RelationshipTypeSpec]:
        """Domain relationships only — the ones drawn as ordinary edges."""
        return dict(self.spec.relationship_types)

    @cached_property
    def system_relationship_types(self) -> dict[str, RelationshipTypeSpec]:
        """Provenance and identity relationships, which the canvas treats specially."""
        return dict(self.spec.system_relationship_types)

    @cached_property
    def all_relationship_types(self) -> dict[str, RelationshipTypeSpec]:
        return {**self.relationship_types, **self.system_relationship_types}

    def relationship_type(self, name: str) -> RelationshipTypeSpec:
        try:
            return self.all_relationship_types[name]
        except KeyError:
            raise OntologyError(f"unknown relationship type {name!r}") from None

    def relationships_from(self, entity_type: str) -> tuple[str, ...]:
        """Relationship types a node of this type may be the source of.

        This is what makes the right-click menu ontology-aware: a Person is
        never offered "issued tender".
        """
        return tuple(
            sorted(
                name
                for name, rel in self.all_relationship_types.items()
                if self._admits(rel.source, entity_type)
            )
        )

    def _admits(self, endpoint_types: list[str], entity_type: str) -> bool:
        resolved = self.entity_type(entity_type)
        return any(
            declared == entity_type or declared in resolved.ancestors for declared in endpoint_types
        )


def load(path: Path | str | None = None) -> Ontology:
    """Read, parse and validate the ontology file."""
    path = Path(path) if path is not None else DEFAULT_ONTOLOGY_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OntologyError(f"ontology file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise OntologyError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise OntologyError(f"{path} must contain a mapping at the top level")

    try:
        spec = OntologySpec.model_validate(raw)
    except Exception as exc:
        raise OntologyError(f"{path} is not a valid ontology:\n{exc}") from exc

    return Ontology(spec)


# --- validation helpers -------------------------------------------------


def _resolve_entity_types(spec: OntologySpec) -> dict[str, ResolvedEntityType]:
    """Fold inherited properties into each type, rejecting cycles as we go."""
    resolved: dict[str, ResolvedEntityType] = {}
    for name in spec.entity_types:
        _resolve_one(name, spec, resolved, seen=())
    return resolved


def _resolve_one(
    name: str,
    spec: OntologySpec,
    resolved: dict[str, ResolvedEntityType],
    seen: tuple[str, ...],
) -> ResolvedEntityType:
    if name in resolved:
        return resolved[name]
    if name in seen:
        cycle = " -> ".join((*seen, name))
        raise OntologyError(f"inheritance cycle in entity types: {cycle}")

    entity = spec.entity_types.get(name)
    if entity is None:
        parent_of = seen[-1] if seen else "<unknown>"
        raise OntologyError(f"{parent_of!r} extends {name!r}, which is not defined")

    properties: dict[str, PropertySpec] = {}
    ancestors: tuple[str, ...] = ()
    if entity.extends:
        parent = _resolve_one(entity.extends, spec, resolved, seen=(*seen, name))
        properties.update(parent.properties)
        ancestors = (entity.extends, *parent.ancestors)

    for prop_name, prop in entity.properties.items():
        inherited = properties.get(prop_name)
        if inherited is not None and inherited.type is not prop.type:
            raise OntologyError(
                f"{name}.{prop_name} redefines an inherited property with a "
                f"different type ({inherited.type.value} -> {prop.type.value})"
            )
        properties[prop_name] = prop

    conflicting = set(properties) & RESERVED_ENTITY_PROPERTIES
    if conflicting:
        raise OntologyError(
            f"{name} declares reserved propert{'y' if len(conflicting) == 1 else 'ies'} "
            f"{sorted(conflicting)}; these are supplied by the system"
        )

    result = ResolvedEntityType(name=name, spec=entity, properties=properties, ancestors=ancestors)
    resolved[name] = result
    return result


def _validate_references(spec: OntologySpec, entity_types: dict[str, ResolvedEntityType]) -> None:
    """Check every name one declaration uses against the others."""
    errors: list[str] = []

    for name, resolved in entity_types.items():
        for identifier in resolved.spec.identifiers:
            if identifier not in resolved.properties:
                errors.append(
                    f"{name}.identifiers names {identifier!r}, which is not a "
                    f"property of {name}"
                )
        for placeholder in _placeholders(resolved.spec.display_name):
            if placeholder not in resolved.properties:
                errors.append(
                    f"{name}.display_name refers to {{{placeholder}}}, which is "
                    f"not a property of {name}"
                )

    all_relationships = {**spec.relationship_types, **spec.system_relationship_types}
    for name, rel in all_relationships.items():
        for role, declared in (("source", rel.source), ("target", rel.target)):
            for endpoint in declared:
                if endpoint not in entity_types:
                    errors.append(
                        f"{name}.{role} names entity type {endpoint!r}, which is " f"not defined"
                    )
        conflicting = set(rel.properties) & RESERVED_RELATIONSHIP_PROPERTIES
        if conflicting:
            errors.append(
                f"{name} declares reserved propert"
                f"{'y' if len(conflicting) == 1 else 'ies'} {sorted(conflicting)}"
            )
        if rel.temporal and {"valid_from", "valid_to"} & set(rel.properties):
            errors.append(
                f"{name} is temporal, so valid_from/valid_to are supplied "
                f"automatically and must not be declared"
            )

    if errors:
        raise OntologyError("\n".join(f"  - {e}" for e in errors))


def _placeholders(template: str) -> list[str]:
    """Return the ``{name}`` placeholders in a display template."""
    return [
        field
        for _, field, _, _ in string.Formatter().parse(template)
        if field and re.fullmatch(r"[a-z][a-z0-9_]*", field)
    ]
