"""Apply the ontology's constraints and indexes to a database.

The statements come from ``ontology/build/constraints.cypher``, generated from
the ontology. They are idempotent, so applying them on every startup is safe
and keeps a developer's database in step with the ontology they have checked
out — for additive changes, at least. A breaking change still needs the
migration that ``sla-ontology diff`` describes.
"""

from __future__ import annotations

from pathlib import Path

from neo4j import AsyncDriver

from sla.ontology.cli import REPO_ROOT

CONSTRAINTS_PATH = REPO_ROOT / "ontology" / "build" / "constraints.cypher"


class SchemaError(Exception):
    """A constraint or index could not be applied to this database."""


def statements(path: Path | None = None) -> list[str]:
    """Split the generated script into individual executable statements.

    Neo4j will not accept several schema commands in one call, so they are run
    one at a time. Comment-only fragments are dropped.
    """
    text = (path or CONSTRAINTS_PATH).read_text(encoding="utf-8")
    result: list[str] = []
    for chunk in text.split(";"):
        lines = [
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]
        if lines:
            result.append("\n".join(lines).strip())
    return result


async def apply(driver: AsyncDriver, database: str, path: Path | None = None) -> int:
    """Apply every constraint and index. Returns how many statements ran."""
    applied = 0
    async with driver.session(database=database) as session:
        for statement in statements(path):
            try:
                await session.run(statement)  # type: ignore[arg-type]
            except Exception as exc:
                first_line = statement.splitlines()[0]
                raise SchemaError(
                    f"could not apply schema statement:\n  {first_line}\n{exc}\n\n"
                    f"A constraint that cannot be created usually means the database "
                    f"predates the current ontology. Check `sla-ontology diff` and run "
                    f"the matching script in migrations/."
                ) from exc
            applied += 1
    return applied


async def drop_all_data(driver: AsyncDriver, database: str) -> None:
    """Delete every node and relationship, leaving the schema in place.

    For tests and for resetting a development database. It does not drop
    constraints, so the schema stays applied.
    """
    async with driver.session(database=database) as session:
        await session.run("MATCH (n) DETACH DELETE n")
