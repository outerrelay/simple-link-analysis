"""Generate the Neo4j constraints and indexes the ontology implies.

Every node carries the labels of its own type and all its supertypes, so a
single uniqueness constraint on ``:Thing(id)`` covers every entity in the
graph. Properties marked ``indexed`` get an index on the concrete labels that
actually have them.

The statements are idempotent (``IF NOT EXISTS``), so applying them repeatedly
is safe. They only ever *add*: dropping an index for a property removed from
the ontology is a migration step, not something a generator should do silently.
"""

from __future__ import annotations

from sla.ontology.generators._common import BANNER
from sla.ontology.loader import Ontology

# The label every entity carries, and the root of the type hierarchy.
ROOT_LABEL = "Thing"


def render(ontology: Ontology) -> str:
    """Render the schema statements as a runnable Cypher script."""
    lines = [f"// {line}" for line in BANNER.splitlines()]
    lines.append(f"// Ontology version: {ontology.version}")
    lines.append("")

    lines.append("// --- Identity -----------------------------------------------------")
    lines.append(
        f"CREATE CONSTRAINT entity_id_unique IF NOT EXISTS\n"
        f"FOR (n:{ROOT_LABEL}) REQUIRE n.id IS UNIQUE;"
    )
    lines.append("")
    lines.append(
        "// Every node records which ontology version it was written under, so a\n"
        "// later migration can find the nodes it still has to convert."
    )
    lines.append(
        f"CREATE INDEX entity_ontology_version IF NOT EXISTS\n"
        f"FOR (n:{ROOT_LABEL}) ON (n.ontology_version);"
    )
    lines.append("")

    lines.extend(_provenance_section())
    lines.extend(_canonical_section(ontology))
    lines.extend(_property_index_section(ontology))

    return "\n".join(lines).rstrip() + "\n"


def _provenance_section() -> list[str]:
    """Constraints for the assertion layer that backs every relationship."""
    return [
        "// --- Provenance ---------------------------------------------------",
        "CREATE CONSTRAINT assertion_id_unique IF NOT EXISTS",
        "FOR (a:Assertion) REQUIRE a.id IS UNIQUE;",
        "",
        "CREATE INDEX assertion_predicate IF NOT EXISTS",
        "FOR (a:Assertion) ON (a.predicate);",
        "",
        "CREATE INDEX assertion_status IF NOT EXISTS",
        "FOR (a:Assertion) ON (a.status);",
        "",
    ]


def _canonical_section(ontology: Ontology) -> list[str]:
    """Uniqueness for every type the ontology declares canonical.

    A canonical type exists to be shared: one node per phone number, per
    identifier, per domain, so that two entities using the same one point at
    the *same* node. That is the only reason to model them as nodes rather
    than properties, and without the constraint nothing enforces it.
    """
    canonical = {
        name: resolved.spec.canonical_key
        for name, resolved in sorted(ontology.concrete_entity_types.items())
        if resolved.spec.canonical_key
    }
    if not canonical:
        return []

    lines = [
        "// --- Canonical nodes ----------------------------------------------",
        "// One node per distinct key, so that two entities sharing a phone",
        "// number or an identifier are attached to the same node and duplicate",
        "// detection is a single hop.",
    ]
    for name, keys in canonical.items():
        properties = ", ".join(f"n.{key}" for key in keys)
        subject = properties if len(keys) == 1 else f"({properties})"
        lines.append(f"CREATE CONSTRAINT {_snake(name)}_canonical IF NOT EXISTS")
        lines.append(f"FOR (n:{name}) REQUIRE {subject} IS UNIQUE;")
        lines.append("")
    return lines


def _property_index_section(ontology: Ontology) -> list[str]:
    """One index per (concrete label, property) pair marked ``indexed``."""
    lines = [
        "// --- Property indexes ---------------------------------------------",
    ]
    emitted = False
    for name, resolved in sorted(ontology.concrete_entity_types.items()):
        indexed = sorted(
            prop_name for prop_name, prop in resolved.properties.items() if prop.indexed
        )
        canonical = set(resolved.spec.canonical_key)
        for prop_name in indexed:
            if prop_name in canonical:
                continue  # already covered by the uniqueness constraint above
            lines.append(
                f"CREATE INDEX {_index_name(name, prop_name)} IF NOT EXISTS\n"
                f"FOR (n:{name}) ON (n.{prop_name});"
            )
            lines.append("")
            emitted = True

    if not emitted:
        lines.append("// (none declared)")
        lines.append("")
    return lines


def _index_name(label: str, prop: str) -> str:
    """A stable, readable index name: ``company_registration_number``."""
    return f"{_snake(label)}_{prop}"


def _snake(label: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(label):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)
