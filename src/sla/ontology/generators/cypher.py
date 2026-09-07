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
    lines.extend(_identifier_section(ontology))
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


def _identifier_section(ontology: Ontology) -> list[str]:
    """The composite index that makes duplicate detection cheap.

    Finding entities that share a strong identifier is the query behind every
    ``SAME_AS`` candidate, so it gets an index on the pair rather than on each
    property separately.
    """
    if "Identifier" not in ontology.entity_types:
        return []
    return [
        "// --- Duplicate detection ------------------------------------------",
        "// Identifier nodes are canonical: one node per (scheme, value), so two",
        "// entities bearing the same identifier point at the *same* node and the",
        "// SAME_AS candidate query is a single hop. Uniqueness enforces that, and",
        "// its backing index makes the lookup cheap.",
        "CREATE CONSTRAINT identifier_scheme_value_unique IF NOT EXISTS",
        "FOR (n:Identifier) REQUIRE (n.scheme, n.value) IS UNIQUE;",
        "",
    ]


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
        for prop_name in indexed:
            if name == "Identifier" and prop_name in {"scheme", "value"}:
                continue  # covered by the composite index above
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
