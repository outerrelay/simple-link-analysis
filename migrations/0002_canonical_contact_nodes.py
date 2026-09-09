#!/usr/bin/env python3
"""Ontology 0.1.0 -> 0.2.0: make phone, email and website canonical.

`sla-ontology diff` reports three breaking changes:

    PhoneNumber.e164     new required property
    EmailAddress.address new required property
    Website.domain       became required

Each existing node needs the new key backfilled before the uniqueness
constraints can be applied. Where two nodes turn out to share a key, they are
*reported, not merged* — merging is the analyst's decision, and this script
will not make it for them.

    python migrations/0002_canonical_contact_nodes.py --dry-run
    python migrations/0002_canonical_contact_nodes.py
"""

from __future__ import annotations

import argparse
import asyncio
import re

from neo4j import AsyncGraphDatabase

from sla.config import get_settings

# Backfill rules: label -> (new key property, source property, normaliser).
RULES = {
    "PhoneNumber": ("e164", "name", lambda v: re.sub(r"[^\d+]", "", v or "") or None),
    "EmailAddress": ("address", "name", lambda v: (v or "").strip().lower() or None),
    "Website": (
        "domain",
        "url",
        lambda v: (
            re.sub(r"^www\.", "", re.sub(r"^https?://", "", (v or "").strip().lower())).split("/")[
                0
            ]
            or None
        ),
    ),
}


# Indexes made redundant by the new uniqueness constraints. Neo4j refuses to
# create a constraint while a plain index covers the same property, so these
# have to go first.
SUPERSEDED_INDEXES = ("website_domain", "identifier_scheme_value")


async def _drop_superseded(session, dry_run: bool) -> None:
    for index in SUPERSEDED_INDEXES:
        if dry_run:
            print(f"  would drop index {index} (superseded by a uniqueness constraint)")
            continue
        await session.run(f"DROP INDEX {index} IF EXISTS")
        print(f"  dropped index {index} if present")


async def migrate(dry_run: bool) -> int:
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        notifications_disabled_categories=["UNRECOGNIZED"],
    )
    problems = 0
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            await _drop_superseded(session, dry_run)
            for label, (key, source, normalise) in RULES.items():
                result = await session.run(
                    f"MATCH (n:{label}) WHERE n.`{key}` IS NULL "
                    f"RETURN n.id AS id, n.`{source}` AS source"
                )
                rows = [record async for record in result]
                if not rows:
                    print(f"{label}: nothing to backfill")
                    continue

                values: dict[str, list[str]] = {}
                for row in rows:
                    value = normalise(row["source"])
                    if value is None:
                        print(
                            f"  ! {label} {row['id']}: cannot derive {key} from {row['source']!r}"
                        )
                        problems += 1
                        continue
                    values.setdefault(value, []).append(row["id"])

                for value, ids in values.items():
                    if len(ids) > 1:
                        print(
                            f"  ! {len(ids)} {label} nodes share {key}={value!r}: {ids}\n"
                            f"    Merge them in the interface before applying the "
                            f"uniqueness constraint."
                        )
                        problems += 1
                        continue
                    if dry_run:
                        print(f"  would set {label} {ids[0]} {key}={value!r}")
                        continue
                    await session.run(
                        f"MATCH (n:{label} {{id: $id}}) SET n.`{key}` = $value",
                        id=ids[0],
                        value=value,
                    )
                print(f"{label}: {len(values)} node(s) handled")
    finally:
        await driver.close()

    if problems:
        print(f"\n{problems} node(s) need attention before the constraints can be applied.")
    elif dry_run:
        print("\nDry run only; re-run without --dry-run to write.")
    else:
        print("\nBackfill complete. Restart the app to apply the new constraints.")
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    raise SystemExit(asyncio.run(migrate(parser.parse_args().dry_run)))
