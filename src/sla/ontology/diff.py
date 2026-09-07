"""Compare two versions of the ontology and classify what changed.

Regenerating code from an edited ontology is easy. The hard part is the data
already in Neo4j, which does not regenerate: renaming a property leaves every
stored node using the old key.

So changes are sorted into two kinds. **Additive** changes are safe to apply to
a populated database — new types, new optional properties, wider relationship
endpoints. **Breaking** changes are not: something already stored means
something different, or no longer exists, afterwards. Each breaking change
carries a note describing the migration it implies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sla.ontology.loader import Ontology
from sla.ontology.spec import PropertySpec, RelationshipTypeSpec


class Impact(str, Enum):
    ADDITIVE = "additive"
    """Safe to apply to a populated database."""

    BREAKING = "breaking"
    """Existing data does not conform afterwards; a migration is required."""


@dataclass(frozen=True)
class Change:
    impact: Impact
    subject: str
    """What changed, e.g. ``Company.legal_form``."""

    description: str
    migration: str = ""
    """For breaking changes, what has to happen to the stored data."""

    def __str__(self) -> str:
        return f"[{self.impact.value}] {self.subject}: {self.description}"


@dataclass(frozen=True)
class OntologyDiff:
    old_version: str
    new_version: str
    changes: tuple[Change, ...]

    @property
    def breaking(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.impact is Impact.BREAKING)

    @property
    def additive(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.impact is Impact.ADDITIVE)

    @property
    def is_safe(self) -> bool:
        """True when the new ontology can be applied without migrating data."""
        return not self.breaking

    def __bool__(self) -> bool:
        return bool(self.changes)


def diff(old: Ontology, new: Ontology) -> OntologyDiff:
    """Classify every difference between two ontologies."""
    changes: list[Change] = []
    changes.extend(_diff_entity_types(old, new))
    changes.extend(_diff_relationship_types(old, new))
    return OntologyDiff(
        old_version=old.version,
        new_version=new.version,
        changes=tuple(sorted(changes, key=lambda c: (c.impact is Impact.ADDITIVE, c.subject))),
    )


def _diff_entity_types(old: Ontology, new: Ontology) -> list[Change]:
    changes: list[Change] = []
    old_types, new_types = old.entity_types, new.entity_types

    for name in sorted(set(new_types) - set(old_types)):
        changes.append(Change(Impact.ADDITIVE, name, "new entity type"))

    for name in sorted(set(old_types) - set(new_types)):
        changes.append(
            Change(
                Impact.BREAKING,
                name,
                "entity type removed",
                migration=f"Delete or re-type existing :{name} nodes before applying.",
            )
        )

    for name in sorted(set(old_types) & set(new_types)):
        old_type, new_type = old_types[name], new_types[name]

        if old_type.abstract != new_type.abstract:
            changes.append(
                Change(
                    Impact.BREAKING,
                    name,
                    f"became {'abstract' if new_type.abstract else 'concrete'}",
                    migration=(
                        f"Existing :{name} nodes must be re-typed to a subtype."
                        if new_type.abstract
                        else "Review whether existing subtype nodes are still correct."
                    ),
                )
            )

        if old_type.ancestors != new_type.ancestors:
            changes.append(
                Change(
                    Impact.BREAKING,
                    name,
                    f"supertypes changed: {list(old_type.ancestors)} -> "
                    f"{list(new_type.ancestors)}",
                    migration=(
                        f"Re-label existing :{name} nodes so their labels match "
                        f"the new inheritance chain."
                    ),
                )
            )

        changes.extend(_diff_properties(name, old_type.properties, new_type.properties, "node"))

    return changes


def _diff_relationship_types(old: Ontology, new: Ontology) -> list[Change]:
    changes: list[Change] = []
    old_rels, new_rels = old.all_relationship_types, new.all_relationship_types

    for name in sorted(set(new_rels) - set(old_rels)):
        changes.append(Change(Impact.ADDITIVE, name, "new relationship type"))

    for name in sorted(set(old_rels) - set(new_rels)):
        changes.append(
            Change(
                Impact.BREAKING,
                name,
                "relationship type removed",
                migration=f"Delete existing [:{name}] relationships before applying.",
            )
        )

    for name in sorted(set(old_rels) & set(new_rels)):
        changes.extend(_diff_one_relationship(name, old_rels[name], new_rels[name], old, new))

    return changes


def _diff_one_relationship(
    name: str,
    old_rel: RelationshipTypeSpec,
    new_rel: RelationshipTypeSpec,
    old: Ontology,
    new: Ontology,
) -> list[Change]:
    changes: list[Change] = []

    if old_rel.directed != new_rel.directed:
        changes.append(
            Change(
                Impact.BREAKING,
                name,
                f"directedness changed to {'directed' if new_rel.directed else 'undirected'}",
                migration=(
                    "Stored edges keep their stored orientation; confirm it still "
                    "carries the intended meaning."
                ),
            )
        )

    if old_rel.temporal and not new_rel.temporal:
        changes.append(
            Change(
                Impact.BREAKING,
                name,
                "no longer temporal",
                migration=f"Remove valid_from/valid_to from existing [:{name}] edges.",
            )
        )
    elif new_rel.temporal and not old_rel.temporal:
        changes.append(
            Change(Impact.ADDITIVE, name, "became temporal; validity dates default to null")
        )

    # Endpoints are compared as concrete types, since that is what constrains
    # the data: widening admits more, narrowing may orphan stored edges.
    for role in ("source", "target"):
        old_types = _concrete_endpoints(old, getattr(old_rel, role))
        new_types = _concrete_endpoints(new, getattr(new_rel, role))
        if removed := sorted(old_types - new_types):
            changes.append(
                Change(
                    Impact.BREAKING,
                    f"{name}.{role}",
                    f"no longer accepts {removed}",
                    migration=(f"Delete [:{name}] edges whose {role} is one of {removed}."),
                )
            )
        if added := sorted(new_types - old_types):
            changes.append(Change(Impact.ADDITIVE, f"{name}.{role}", f"now also accepts {added}"))

    changes.extend(_diff_properties(name, old_rel.properties, new_rel.properties, "relationship"))
    return changes


def _concrete_endpoints(ontology: Ontology, declared: list[str]) -> set[str]:
    return {
        concrete
        for name in declared
        if name in ontology.entity_types
        for concrete in ontology.concrete_subtypes(name)
    }


def _diff_properties(
    owner: str,
    old_props: dict[str, PropertySpec],
    new_props: dict[str, PropertySpec],
    kind: str,
) -> list[Change]:
    changes: list[Change] = []

    for prop in sorted(set(new_props) - set(old_props)):
        spec = new_props[prop]
        if spec.required:
            changes.append(
                Change(
                    Impact.BREAKING,
                    f"{owner}.{prop}",
                    "new required property",
                    migration=(
                        f"Backfill {prop!r} on every existing {owner} {kind}, or "
                        f"make the property optional."
                    ),
                )
            )
        else:
            changes.append(Change(Impact.ADDITIVE, f"{owner}.{prop}", "new optional property"))

    for prop in sorted(set(old_props) - set(new_props)):
        changes.append(
            Change(
                Impact.BREAKING,
                f"{owner}.{prop}",
                "property removed",
                migration=(
                    f"Drop {prop!r} from existing {owner} {kind}s. If this is a "
                    f"rename, copy the value to the new property first."
                ),
            )
        )

    for prop in sorted(set(old_props) & set(new_props)):
        changes.extend(_diff_one_property(f"{owner}.{prop}", old_props[prop], new_props[prop]))

    return changes


def _diff_one_property(
    subject: str, old_prop: PropertySpec, new_prop: PropertySpec
) -> list[Change]:
    changes: list[Change] = []

    if old_prop.type is not new_prop.type:
        changes.append(
            Change(
                Impact.BREAKING,
                subject,
                f"type changed: {old_prop.type.value} -> {new_prop.type.value}",
                migration="Convert stored values to the new type.",
            )
        )

    if old_prop.multi != new_prop.multi:
        changes.append(
            Change(
                Impact.BREAKING,
                subject,
                f"cardinality changed to {'multi' if new_prop.multi else 'single'}",
                migration=(
                    "Wrap stored values in a list."
                    if new_prop.multi
                    else "Collapse stored lists to a single value; excess values are lost."
                ),
            )
        )

    if new_prop.required and not old_prop.required:
        changes.append(
            Change(
                Impact.BREAKING,
                subject,
                "became required",
                migration="Backfill the property wherever it is currently null.",
            )
        )
    elif old_prop.required and not new_prop.required:
        changes.append(Change(Impact.ADDITIVE, subject, "became optional"))

    old_values = set(old_prop.enum or ())
    new_values = set(new_prop.enum or ())
    if removed := sorted(old_values - new_values):
        changes.append(
            Change(
                Impact.BREAKING,
                subject,
                f"enum values removed: {removed}",
                migration=f"Re-map stored values in {removed} to a value that remains.",
            )
        )
    if added := sorted(new_values - old_values):
        changes.append(Change(Impact.ADDITIVE, subject, f"enum values added: {added}"))

    if old_prop.indexed != new_prop.indexed:
        changes.append(
            Change(
                Impact.ADDITIVE,
                subject,
                f"index {'added' if new_prop.indexed else 'no longer declared'}",
            )
        )

    return changes
