"""The three meanings of removing something.

Removing a node from a chart is the application store's business. Here the
distinction is between suppressing a node — hiding it durably, so re-ingestion
does not bring it back — and deleting it outright.
"""

from __future__ import annotations

from datetime import UTC

from sla.graph.model import EntityRecord
from sla.graph.repository import GraphRepository


async def entity(repository: GraphRepository, type_name: str, name: str, **properties):
    return await repository.upsert_entity(
        EntityRecord(type=type_name, properties={"name": name, **properties})
    )


async def test_suppressed_entity_disappears_from_queries(repository: GraphRepository) -> None:
    company = await entity(repository, "Company", "Acme AS")

    await repository.suppress_entity(company.id)

    assert await repository.get_entity(company.id) is None
    assert await repository.find_entities(entity_type="Company") == []


async def test_suppressed_entity_is_still_there_if_you_ask_for_it(
    repository: GraphRepository,
) -> None:
    """Suppression hides; it does not destroy the record or its history."""
    company = await entity(repository, "Company", "Acme AS")
    await repository.suppress_entity(company.id)

    found = await repository.get_entity(company.id, include_suppressed=True)

    assert found is not None
    assert found.suppressed is True


async def test_re_ingesting_does_not_resurrect_a_suppressed_entity(
    repository: GraphRepository,
) -> None:
    """The point of the tombstone: rejecting a proposal has to stick."""
    company = await entity(repository, "Company", "Acme AS")
    await repository.suppress_entity(company.id)

    await repository.upsert_entity(
        EntityRecord(id=company.id, type="Company", properties={"name": "Acme AS"})
    )

    assert await repository.get_entity(company.id) is None


async def test_suppression_can_be_lifted(repository: GraphRepository) -> None:
    company = await entity(repository, "Company", "Acme AS")
    await repository.suppress_entity(company.id)

    await repository.suppress_entity(company.id, suppressed=False)

    assert await repository.get_entity(company.id) is not None


async def test_suppressed_nodes_are_not_traversed(repository: GraphRepository) -> None:
    person = await entity(repository, "Person", "A. Owner")
    company = await entity(repository, "Company", "Acme AS")
    await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )

    await repository.suppress_entity(company.id)

    neighbourhood = await repository.expand([person.id])
    assert [e.id for e in neighbourhood.entities] == [person.id]
    assert neighbourhood.relationships == []


async def test_deleting_removes_the_node_its_edges_and_its_assertions(
    repository: GraphRepository, neo4j_driver
) -> None:
    person = await entity(repository, "Person", "A. Owner")
    company = await entity(repository, "Company", "Acme AS")
    assertion, _ = await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )

    assert await repository.delete_entity(company.id) is True

    assert await repository.get_entity(company.id, include_suppressed=True) is None
    async with neo4j_driver.session() as session:
        result = await session.run(
            "MATCH (a:Assertion {id: $id}) RETURN count(a) AS n", id=assertion.id
        )
        assert (await result.single())["n"] == 0, "claims about a deleted node are meaningless"


async def test_deleting_an_entity_leaves_its_sources_alone(
    repository: GraphRepository,
) -> None:
    """A document keeps existing even when what it described is deleted."""
    from datetime import datetime

    person = await entity(repository, "Person", "A. Owner")
    company = await entity(repository, "Company", "Acme AS")
    report = await entity(
        repository,
        "Document",
        "Filing.pdf",
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id, source_id=report.id
    )

    await repository.delete_entity(company.id)

    assert await repository.get_entity(report.id) is not None


async def test_deleting_something_that_is_not_there_reports_it(
    repository: GraphRepository,
) -> None:
    assert await repository.delete_entity("no-such-id") is False
