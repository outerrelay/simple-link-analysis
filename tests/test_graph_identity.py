"""Duplicate detection: propose, never merge."""

from __future__ import annotations

from datetime import UTC, datetime

from sla.graph.identity import IdentityResolver
from sla.graph.model import EntityRecord
from sla.graph.repository import GraphRepository


async def entity(repository: GraphRepository, type_name: str, name: str, **properties):
    return await repository.upsert_entity(
        EntityRecord(type=type_name, properties={"name": name, **properties})
    )


async def with_lei(repository: GraphRepository, company_name: str, lei: str):
    """A company bearing an LEI, linked through an Identifier node."""
    company = await entity(repository, "Company", company_name)
    identifier = await repository.upsert_identifier("lei", lei)
    await repository.assert_relationship(
        predicate="HAS_IDENTIFIER", subject_id=company.id, object_id=identifier.id
    )
    return company


async def test_shared_identifier_proposes_a_candidate(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    left = await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    right = await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")

    candidates = await resolver.propose_candidates()

    assert len(candidates) == 1
    assert {candidates[0].left_id, candidates[0].right_id} == {left.id, right.id}
    assert "5493001KJTIIGC8Y1R12" in candidates[0].basis


async def test_different_identifiers_propose_nothing(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    await with_lei(repository, "Beta AS", "213800LBQA1XJIQ7XZ44")

    assert await resolver.propose_candidates() == []


async def test_a_declared_identifier_property_also_proposes_candidates(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    """Document.content_hash is a strong identifier, so the same file twice matches."""
    retrieved = datetime(2024, 1, 1, tzinfo=UTC)
    await entity(
        repository, "Document", "report.pdf", retrieved_at=retrieved, content_hash="abc123"
    )
    await entity(
        repository, "Document", "report-copy.pdf", retrieved_at=retrieved, content_hash="abc123"
    )

    candidates = await resolver.propose_candidates()

    assert len(candidates) == 1
    assert "content_hash" in candidates[0].basis


async def test_nothing_is_ever_merged(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    """Both nodes survive; only a candidate relationship is added."""
    left = await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    right = await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")

    await resolver.propose_candidates()

    assert await repository.get_entity(left.id) is not None
    assert await repository.get_entity(right.id) is not None


async def test_proposing_twice_does_not_duplicate(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")

    first = await resolver.propose_candidates()
    second = await resolver.propose_candidates()

    assert len(first) == 1
    assert second == [], "re-running should be quiet once the queue is worked through"


async def test_a_rejected_pair_is_not_proposed_again(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    """An analyst's decision must survive re-running detection."""
    await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")
    await resolver.propose_candidates()
    pending = await resolver.pending_review()
    await resolver.decide(pending[0]["id"], confirmed=False, decided_by="analyst")

    assert await resolver.propose_candidates() == []
    assert await resolver.pending_review() == []


async def test_review_queue_carries_enough_to_decide(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")
    await resolver.propose_candidates()

    pending = await resolver.pending_review()

    assert len(pending) == 1
    row = pending[0]
    assert {row["left_name"], row["right_name"]} == {"Acme AS", "ACME A/S"}
    assert row["left_type"] == row["right_type"] == "Company"
    assert row["basis"]


async def test_confirming_records_who_decided(
    repository: GraphRepository, resolver: IdentityResolver, neo4j_driver
) -> None:
    await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")
    await resolver.propose_candidates()
    pending = await resolver.pending_review()

    await resolver.decide(pending[0]["id"], confirmed=True, decided_by="analyst")

    async with neo4j_driver.session() as session:
        result = await session.run(
            "MATCH ()-[r:SAME_AS {id: $id}]-() RETURN r.status AS status, r.decided_by AS by",
            id=pending[0]["id"],
        )
        record = await result.single()
    assert record["status"] == "confirmed"
    assert record["by"] == "analyst"


async def test_suppressed_entities_are_not_proposed(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    left = await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")
    await repository.suppress_entity(left.id)

    assert await resolver.propose_candidates() == []


async def test_same_as_candidates_do_not_pollute_expansion(
    repository: GraphRepository, resolver: IdentityResolver
) -> None:
    """A candidate is a proposal, not a fact about the network."""
    left = await with_lei(repository, "Acme AS", "5493001KJTIIGC8Y1R12")
    await with_lei(repository, "ACME A/S", "5493001KJTIIGC8Y1R12")
    await resolver.propose_candidates()

    neighbourhood = await repository.expand([left.id], relationship_types=["HAS_IDENTIFIER"])

    assert all(r.type == "HAS_IDENTIFIER" for r in neighbourhood.relationships)
