"""Populate a database with a small worked example.

A plausible corporate structure with people, addresses, a shared registered
office, an identifier collision that should surface as a duplicate candidate,
and a document backing one of the claims — enough to exercise the canvas, the
temporal filter and the review queue.

    python -m sla.seed
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime

from neo4j import AsyncGraphDatabase

from sla.config import get_settings
from sla.graph import schema
from sla.graph.identity import IdentityResolver
from sla.graph.model import EntityRecord, ExtractionMethod
from sla.graph.repository import GraphRepository
from sla.ontology import load


async def build(repository: GraphRepository, resolver: IdentityResolver) -> None:
    async def entity(type_name: str, name: str, **properties) -> EntityRecord:
        return await repository.upsert_entity(
            EntityRecord(type=type_name, properties={"name": name, **properties})
        )

    # --- people ---
    wiedenmann = await entity(
        "Person",
        "Markus Wiedenmann",
        first_name="Markus",
        last_name="Wiedenmann",
        birth_date=date(1968, 4, 12),
        nationality=["DE"],
        title="Dr",
    )
    hausmann = await entity(
        "Person", "Elke Hausmann", first_name="Elke", last_name="Hausmann", nationality=["DE"]
    )
    berg = await entity(
        "Person", "Anders Berg", first_name="Anders", last_name="Berg", nationality=["NO"]
    )

    # --- companies ---
    holding = await entity(
        "Company",
        "Wiedenmann Vermögensverwaltung GmbH",
        jurisdiction="DE",
        registration_number="HRB 214553",
        legal_form="GmbH",
        incorporation_date=date(2004, 9, 1),
        status="active",
    )
    heussallee = await entity(
        "Company",
        "Entwicklungsgesellschaft Heussallee Verwaltung GmbH",
        jurisdiction="DE",
        registration_number="HRB 198220",
        legal_form="GmbH",
        status="active",
    )
    nordic = await entity(
        "Company",
        "Nordic Infrastructure AS",
        jurisdiction="NO",
        registration_number="912345678",
        legal_form="AS",
        status="active",
    )
    # Same company, filed under a slightly different name — the duplicate the
    # LEI collision should surface for review.
    nordic_alias = await entity(
        "Company", "Nordic Infrastructure A/S", jurisdiction="NO", legal_form="AS"
    )

    # --- addresses, shared registered office ---
    bonn = await entity(
        "Address",
        "Heussallee 12, 53113 Bonn",
        street="Heussallee 12",
        city="Bonn",
        postal_code="53113",
        country="DE",
    )
    oslo = await entity(
        "Address",
        "Karl Johans gate 8, 0154 Oslo",
        street="Karl Johans gate 8",
        city="Oslo",
        postal_code="0154",
        country="NO",
    )

    # --- an identifier both Nordic records bear ---
    lei = await repository.upsert_identifier("lei", "5493001KJTIIGC8Y1R12", authority="GLEIF")

    # --- a document supporting one of the claims ---
    filing = await entity(
        "Document",
        "Handelsregister extract HRB 214553.pdf",
        retrieved_at=datetime(2025, 11, 3, tzinfo=UTC),
        filename="hrb-214553.pdf",
        media_type="application/pdf",
        page_count=4,
        published_date=date(2025, 10, 28),
    )

    tender = await entity(
        "PublicTender",
        "E18 Bridge maintenance framework",
        reference="2024/S 118-334210",
        published_date=date(2024, 6, 18),
        estimated_amount=48_000_000,
        currency="NOK",
    )

    async def relate(predicate: str, subject, obj, **kwargs):
        await repository.assert_relationship(
            predicate=predicate, subject_id=subject.id, object_id=obj.id, **kwargs
        )

    await relate(
        "OWNS",
        wiedenmann,
        holding,
        properties={"percentage": 100.0},
        valid_from=date(2004, 9, 1),
        source_id=filing.id,
        method=ExtractionMethod.CONNECTOR,
    )
    await relate(
        "DIRECTOR_OF",
        wiedenmann,
        holding,
        properties={"role": "geschäftsführer"},
        valid_from=date(2004, 9, 1),
        source_id=filing.id,
        method=ExtractionMethod.CONNECTOR,
    )
    await relate("PARENT_OF", holding, heussallee, valid_from=date(2011, 2, 1))
    await relate(
        "OWNS", holding, nordic, properties={"percentage": 62.5}, valid_from=date(2016, 5, 1)
    )
    # A directorship that has ended: hidden when the canvas is set to today.
    await relate(
        "DIRECTOR_OF",
        hausmann,
        heussallee,
        properties={"role": "geschäftsführerin"},
        valid_from=date(2012, 1, 1),
        valid_to=date(2019, 3, 31),
    )
    await relate(
        "DIRECTOR_OF",
        berg,
        nordic,
        properties={"role": "daglig leder"},
        valid_from=date(2016, 5, 1),
    )
    await relate("REGISTERED_AT", holding, bonn, properties={"address_type": "registered"})
    await relate("REGISTERED_AT", heussallee, bonn, properties={"address_type": "registered"})
    await relate("REGISTERED_AT", nordic, oslo, properties={"address_type": "registered"})
    await relate("RESIDES_AT", wiedenmann, bonn)
    await relate("HAS_IDENTIFIER", nordic, lei)
    await relate("HAS_IDENTIFIER", nordic_alias, lei)
    await relate("MENTIONS", filing, wiedenmann, properties={"page": 2})
    await relate("MENTIONS", filing, holding, properties={"page": 1})
    await relate("ISSUED_TENDER", nordic, tender)
    await relate(
        "AWARDED", heussallee, tender, properties={"amount": 41_200_000, "currency": "NOK"}
    )

    proposed = await resolver.propose_candidates()
    print(f"  duplicate candidates proposed: {len(proposed)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete everything first")
    args = parser.parse_args()

    settings = get_settings()
    ontology = load()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        notifications_disabled_categories=["UNRECOGNIZED"],
    )
    try:
        await schema.apply(driver, settings.neo4j_database)
        if args.reset:
            await schema.drop_all_data(driver, settings.neo4j_database)
            print("  cleared existing data")
        repository = GraphRepository(driver, ontology, settings.neo4j_database)
        resolver = IdentityResolver(driver, ontology, settings.neo4j_database)
        await build(repository, resolver)
        print("  seed complete")
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
