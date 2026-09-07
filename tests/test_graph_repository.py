"""The graph store, exercised against a live Neo4j."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from sla.graph.model import EntityRecord, ExtractionMethod
from sla.graph.repository import GraphError, GraphRepository


async def make_company(repository: GraphRepository, name: str, **properties) -> EntityRecord:
    return await repository.upsert_entity(
        EntityRecord(type="Company", properties={"name": name, **properties})
    )


async def make_person(repository: GraphRepository, name: str, **properties) -> EntityRecord:
    return await repository.upsert_entity(
        EntityRecord(type="Person", properties={"name": name, **properties})
    )


# --- entities -----------------------------------------------------------


async def test_entity_round_trips(repository: GraphRepository) -> None:
    stored = await make_company(
        repository, "Acme AS", jurisdiction="NO", registration_number="912345678"
    )

    fetched = await repository.get_entity(stored.id)

    assert fetched is not None
    assert fetched.type == "Company"
    assert fetched.properties["name"] == "Acme AS"
    assert fetched.properties["registration_number"] == "912345678"
    assert fetched.ontology_version


async def test_entity_carries_its_supertype_labels(
    repository: GraphRepository, neo4j_driver
) -> None:
    """Inherited labels are what let one constraint cover every node."""
    company = await make_company(repository, "Acme AS")

    async with neo4j_driver.session() as session:
        result = await session.run("MATCH (n {id: $id}) RETURN labels(n) AS labels", id=company.id)
        record = await result.single()

    assert set(record["labels"]) == {"Company", "LegalEntity", "Thing"}


async def test_upsert_updates_rather_than_duplicating(repository: GraphRepository) -> None:
    first = await make_company(repository, "Acme AS")
    first.properties["legal_form"] = "AS"
    await repository.upsert_entity(first)

    found = await repository.find_entities(entity_type="Company")

    assert len(found) == 1
    assert found[0].properties["legal_form"] == "AS"


async def test_undeclared_property_is_rejected(repository: GraphRepository) -> None:
    """Neo4j is schemaless, so a typo would otherwise become a silent new field."""
    with pytest.raises(GraphError, match="has no propert"):
        await make_company(repository, "Acme AS", turnover=1_000_000)


async def test_missing_required_property_is_rejected(repository: GraphRepository) -> None:
    with pytest.raises(GraphError, match="requires"):
        await repository.upsert_entity(EntityRecord(type="Company", properties={}))


async def test_abstract_types_cannot_be_instantiated(repository: GraphRepository) -> None:
    with pytest.raises(GraphError, match="abstract"):
        await repository.upsert_entity(EntityRecord(type="LegalEntity", properties={"name": "x"}))


# --- assertions and the edges derived from them --------------------------


async def test_asserting_creates_both_the_claim_and_the_edge(repository: GraphRepository) -> None:
    person = await make_person(repository, "A. Owner")
    company = await make_company(repository, "Acme AS")

    assertion, edge = await repository.assert_relationship(
        predicate="OWNS",
        subject_id=person.id,
        object_id=company.id,
        properties={"percentage": 51.0},
        valid_from=date(2019, 3, 1),
    )

    assert edge.type == "OWNS"
    assert edge.properties["percentage"] == 51.0
    assert edge.valid_from == date(2019, 3, 1)
    assert assertion.id in edge.assertion_ids


async def test_a_second_source_adds_support_without_duplicating_the_edge(
    repository: GraphRepository,
) -> None:
    person = await make_person(repository, "A. Owner")
    company = await make_company(repository, "Acme AS")

    first, _ = await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )
    second, edge = await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id, confidence=0.8
    )

    assert sorted(edge.assertion_ids) == sorted([first.id, second.id])
    neighbourhood = await repository.expand([person.id])
    assert len(neighbourhood.relationships) == 1, "one edge, two supporting claims"


async def test_edge_survives_while_another_assertion_supports_it(
    repository: GraphRepository,
) -> None:
    """The whole point of keeping claims and edges apart."""
    person = await make_person(repository, "A. Owner")
    company = await make_company(repository, "Acme AS")
    first, _ = await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )
    await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )

    await repository.retract_assertion(first.id)

    neighbourhood = await repository.expand([person.id])
    assert len(neighbourhood.relationships) == 1
    assert first.id not in neighbourhood.relationships[0].assertion_ids


async def test_retracting_the_last_assertion_removes_the_edge(
    repository: GraphRepository,
) -> None:
    person = await make_person(repository, "A. Owner")
    company = await make_company(repository, "Acme AS")
    assertion, _ = await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )

    assert await repository.retract_assertion(assertion.id) is True

    neighbourhood = await repository.expand([person.id])
    assert neighbourhood.relationships == []


async def test_assertions_record_where_the_claim_came_from(repository: GraphRepository) -> None:
    """The 'how do you know that?' query."""
    person = await make_person(repository, "A. Owner")
    company = await make_company(repository, "Acme AS")
    document = await repository.upsert_entity(
        EntityRecord(
            type="Document",
            properties={
                "name": "Share register 2019.pdf",
                "retrieved_at": datetime(2024, 5, 1, tzinfo=UTC),
                "filename": "sr.pdf",
            },
        )
    )

    assertion, _ = await repository.assert_relationship(
        predicate="OWNS",
        subject_id=person.id,
        object_id=company.id,
        source_id=document.id,
        method=ExtractionMethod.LANGUAGE_MODEL,
        model="claude-opus-5",
        confidence=0.85,
    )

    claims = await repository.assertions_for(person.id, "OWNS", company.id)
    sources = await repository.sources_for_assertion(assertion.id)

    assert len(claims) == 1
    assert claims[0].method is ExtractionMethod.LANGUAGE_MODEL
    assert claims[0].model == "claude-opus-5"
    assert claims[0].confidence == 0.85
    assert [s.id for s in sources] == [document.id]


async def test_relationship_endpoints_are_checked_against_the_ontology(
    repository: GraphRepository,
) -> None:
    company = await make_company(repository, "Acme AS")
    tender = await repository.upsert_entity(
        EntityRecord(type="PublicTender", properties={"name": "Bridge works"})
    )

    with pytest.raises(GraphError, match="does not accept"):
        await repository.assert_relationship(
            predicate="ISSUED_TENDER", subject_id=tender.id, object_id=company.id
        )


async def test_non_temporal_relationship_rejects_validity_dates(
    repository: GraphRepository,
) -> None:
    company = await make_company(repository, "Acme AS")
    tender = await repository.upsert_entity(
        EntityRecord(type="PublicTender", properties={"name": "Bridge works"})
    )

    with pytest.raises(GraphError, match="not temporal"):
        await repository.assert_relationship(
            predicate="ISSUED_TENDER",
            subject_id=company.id,
            object_id=tender.id,
            valid_from=date(2020, 1, 1),
        )


async def test_asserting_against_a_missing_endpoint_fails(repository: GraphRepository) -> None:
    company = await make_company(repository, "Acme AS")

    with pytest.raises(GraphError, match="does not exist"):
        await repository.assert_relationship(
            predicate="OWNS", subject_id="no-such-id", object_id=company.id
        )
