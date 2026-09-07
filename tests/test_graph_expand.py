"""Traversal: source hiding, temporal filtering, depth and truncation."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from sla.graph.model import EntityRecord
from sla.graph.repository import GraphError, GraphRepository


async def entity(repository: GraphRepository, type_name: str, name: str, **properties):
    return await repository.upsert_entity(
        EntityRecord(type=type_name, properties={"name": name, **properties})
    )


async def document(repository: GraphRepository, name: str):
    return await entity(repository, "Document", name, retrieved_at=datetime(2024, 1, 1, tzinfo=UTC))


async def test_expand_returns_neighbours(repository: GraphRepository) -> None:
    person = await entity(repository, "Person", "A. Owner")
    company = await entity(repository, "Company", "Acme AS")
    await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )

    neighbourhood = await repository.expand([person.id])

    assert {e.id for e in neighbourhood.entities} == {person.id, company.id}
    assert len(neighbourhood.relationships) == 1


async def test_isolated_node_still_returns_itself(repository: GraphRepository) -> None:
    person = await entity(repository, "Person", "A. Loner")

    neighbourhood = await repository.expand([person.id])

    assert [e.id for e in neighbourhood.entities] == [person.id]
    assert neighbourhood.relationships == []


async def test_sources_are_hidden_unless_asked_for(repository: GraphRepository) -> None:
    """Documents would otherwise crowd out the network being analysed."""
    person = await entity(repository, "Person", "A. Owner")
    report = await document(repository, "Annual report.pdf")
    await repository.assert_relationship(
        predicate="MENTIONS", subject_id=report.id, object_id=person.id
    )

    without = await repository.expand([person.id])
    with_sources = await repository.expand([person.id], include_sources=True)

    assert {e.id for e in without.entities} == {person.id}
    assert {e.id for e in with_sources.entities} == {person.id, report.id}


async def test_expanding_a_source_you_selected_still_works(repository: GraphRepository) -> None:
    """Hiding sources must not make a selected document un-expandable."""
    person = await entity(repository, "Person", "A. Owner")
    report = await document(repository, "Annual report.pdf")
    await repository.assert_relationship(
        predicate="MENTIONS", subject_id=report.id, object_id=person.id
    )

    neighbourhood = await repository.expand([report.id])

    assert {e.id for e in neighbourhood.entities} == {report.id, person.id}


async def test_depth_controls_how_far_expansion_reaches(repository: GraphRepository) -> None:
    person = await entity(repository, "Person", "A. Owner")
    holding = await entity(repository, "Company", "Holding AS")
    subsidiary = await entity(repository, "Company", "Subsidiary AS")
    await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=holding.id
    )
    await repository.assert_relationship(
        predicate="PARENT_OF", subject_id=holding.id, object_id=subsidiary.id
    )

    shallow = await repository.expand([person.id], depth=1)
    deep = await repository.expand([person.id], depth=2)

    assert {e.id for e in shallow.entities} == {person.id, holding.id}
    assert {e.id for e in deep.entities} == {person.id, holding.id, subsidiary.id}


async def test_expansion_can_be_filtered_by_relationship_type(
    repository: GraphRepository,
) -> None:
    person = await entity(repository, "Person", "A. Owner")
    company = await entity(repository, "Company", "Acme AS")
    address = await entity(repository, "Address", "1 Main St")
    await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )
    await repository.assert_relationship(
        predicate="RESIDES_AT", subject_id=person.id, object_id=address.id
    )

    owns_only = await repository.expand([person.id], relationship_types=["OWNS"])

    assert {e.id for e in owns_only.entities} == {person.id, company.id}


async def test_as_of_returns_the_graph_as_it_stood(repository: GraphRepository) -> None:
    """Directorships end, so 'who ran this in 2020' is a routine question."""
    person = await entity(repository, "Person", "A. Director")
    company = await entity(repository, "Company", "Acme AS")
    await repository.assert_relationship(
        predicate="DIRECTOR_OF",
        subject_id=person.id,
        object_id=company.id,
        valid_from=date(2018, 1, 1),
        valid_to=date(2021, 6, 30),
    )

    during = await repository.expand([person.id], as_of=date(2020, 1, 1))
    after = await repository.expand([person.id], as_of=date(2023, 1, 1))

    assert len(during.relationships) == 1
    assert after.relationships == [], "the directorship had ended by 2023"


async def test_open_ended_relationships_are_still_current(repository: GraphRepository) -> None:
    person = await entity(repository, "Person", "A. Director")
    company = await entity(repository, "Company", "Acme AS")
    await repository.assert_relationship(
        predicate="DIRECTOR_OF",
        subject_id=person.id,
        object_id=company.id,
        valid_from=date(2018, 1, 1),
    )

    now = await repository.expand([person.id], as_of=date(2026, 1, 1))

    assert len(now.relationships) == 1


async def test_expansion_reports_when_it_was_truncated(repository: GraphRepository) -> None:
    """A high-degree node must not be allowed to flood the canvas."""
    hub = await entity(repository, "Company", "Registered Agent Ltd")
    for index in range(6):
        director = await entity(repository, "Person", f"Director {index}")
        await repository.assert_relationship(
            predicate="DIRECTOR_OF", subject_id=director.id, object_id=hub.id
        )

    capped = await repository.expand([hub.id], limit=3)

    assert capped.truncated is True
    assert len(capped.relationships) == 3
    assert await repository.degree(hub.id) == 6


async def test_depth_must_be_positive(repository: GraphRepository) -> None:
    with pytest.raises(GraphError, match="at least 1"):
        await repository.expand(["any"], depth=0)


async def test_unknown_relationship_filter_is_rejected(repository: GraphRepository) -> None:
    with pytest.raises(Exception, match="unknown relationship type"):
        await repository.expand(["any"], relationship_types=["NOT_A_TYPE"])
