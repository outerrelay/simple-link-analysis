"""Merging two records that turn out to be one thing.

Never automatic: every merge here is an explicit instruction. What is tested
is that it loses nothing — edges, provenance and the losing name all survive,
and the whole thing can be undone.
"""

from __future__ import annotations

import pytest

from sla.graph.merge import MergeError, MergeService
from sla.graph.model import EntityRecord, new_id


@pytest.fixture
def merger(neo4j_driver, ontology):
    from tests.conftest import scratch_neo4j_settings

    return MergeService(neo4j_driver, ontology, scratch_neo4j_settings().neo4j_database)


async def company(repository, name: str, **properties) -> EntityRecord:
    return await repository.upsert_entity(
        EntityRecord(type="Company", properties={"name": name, **properties})
    )


async def person(repository, name: str, **properties) -> EntityRecord:
    return await repository.upsert_entity(
        EntityRecord(type="Person", properties={"name": name, **properties})
    )


# --- refusals ---------------------------------------------------------------


async def test_a_node_cannot_be_merged_into_itself(repository, merger) -> None:
    acme = await company(repository, "Acme AS")

    with pytest.raises(MergeError, match="into itself"):
        await merger.preview(acme.id, acme.id)


async def test_types_must_match(repository, merger) -> None:
    """A person is not a company, however similar the names."""
    acme = await company(repository, "Acme AS")
    someone = await person(repository, "Acme")

    with pytest.raises(MergeError, match="only joins records of the same type"):
        await merger.preview(acme.id, someone.id)


async def test_a_missing_node_is_refused(repository, merger) -> None:
    acme = await company(repository, "Acme AS")

    with pytest.raises(MergeError, match="no entity"):
        await merger.preview(acme.id, "no-such-id")


async def test_an_already_merged_node_cannot_be_merged_again(repository, merger) -> None:
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")
    third = await company(repository, "Acme Limited")
    await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    with pytest.raises(MergeError, match="already been merged away"):
        await merger.merge(third.id, absorbed.id, merge_id=new_id())


# --- preview ----------------------------------------------------------------


async def test_preview_reports_conflicts_and_gains(repository, merger) -> None:
    survivor = await company(repository, "Acme AS", jurisdiction="NO")
    absorbed = await company(
        repository, "ACME A/S", jurisdiction="SE", registration_number="912345678"
    )

    preview = await merger.preview(survivor.id, absorbed.id)

    conflicts = {c.name: (c.survivor_value, c.absorbed_value) for c in preview.conflicts}
    assert conflicts["jurisdiction"] == ("NO", "SE")
    assert conflicts["name"] == ("Acme AS", "ACME A/S")
    assert preview.gained_properties["registration_number"] == "912345678"


async def test_preview_counts_what_would_move(repository, merger) -> None:
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")
    director = await person(repository, "A. Director")
    address = await repository.upsert_entity(
        EntityRecord(type="Address", properties={"name": "1 Main St"})
    )
    # Shared: both companies have this director, so the edges collapse into one.
    await repository.assert_relationship(
        predicate="DIRECTOR_OF", subject_id=director.id, object_id=survivor.id
    )
    await repository.assert_relationship(
        predicate="DIRECTOR_OF", subject_id=director.id, object_id=absorbed.id
    )
    # Only the absorbed one has this, so it moves.
    await repository.assert_relationship(
        predicate="REGISTERED_AT", subject_id=absorbed.id, object_id=address.id
    )

    preview = await merger.preview(survivor.id, absorbed.id)

    assert preview.edges_to_collapse == 1
    assert preview.edges_to_move == 1


async def test_preview_changes_nothing(repository, merger) -> None:
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S", registration_number="912345678")

    await merger.preview(survivor.id, absorbed.id)

    assert await repository.get_entity(absorbed.id) is not None
    assert "registration_number" not in (await repository.get_entity(survivor.id)).properties


# --- merging ----------------------------------------------------------------


async def test_the_survivor_gains_what_it_lacked(repository, merger) -> None:
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S", registration_number="912345678")

    await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    kept = await repository.get_entity(survivor.id)
    assert kept.properties["registration_number"] == "912345678"


async def test_the_survivor_keeps_its_own_values_by_default(repository, merger) -> None:
    survivor = await company(repository, "Acme AS", jurisdiction="NO")
    absorbed = await company(repository, "ACME A/S", jurisdiction="SE")

    await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    assert (await repository.get_entity(survivor.id)).properties["jurisdiction"] == "NO"


async def test_conflicts_can_be_resolved_field_by_field(repository, merger) -> None:
    survivor = await company(repository, "Acme AS", jurisdiction="NO")
    absorbed = await company(repository, "ACME A/S", jurisdiction="SE")

    await merger.merge(
        survivor.id, absorbed.id, resolutions={"jurisdiction": "SE"}, merge_id=new_id()
    )

    assert (await repository.get_entity(survivor.id)).properties["jurisdiction"] == "SE"


async def test_the_losing_name_is_kept_as_an_alias(repository, merger) -> None:
    """It is how the company appears in some source; searching should find it."""
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")

    await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    assert "ACME A/S" in (await repository.get_entity(survivor.id)).properties["aliases"]


async def test_multi_valued_properties_are_unioned(repository, merger) -> None:
    survivor = await company(repository, "Acme AS", sector=["64209"])
    absorbed = await company(repository, "ACME A/S", sector=["70100"])

    await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    assert set((await repository.get_entity(survivor.id)).properties["sector"]) == {
        "64209",
        "70100",
    }


async def test_edges_move_to_the_survivor(repository, merger) -> None:
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")
    address = await repository.upsert_entity(
        EntityRecord(type="Address", properties={"name": "1 Main St"})
    )
    await repository.assert_relationship(
        predicate="REGISTERED_AT", subject_id=absorbed.id, object_id=address.id
    )

    result, _ = await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    neighbourhood = await repository.expand([survivor.id])
    assert result.edges_moved == 1
    assert address.id in {e.id for e in neighbourhood.entities}


async def test_duplicate_edges_collapse_and_keep_both_assertions(repository, merger) -> None:
    """Two claims about one relationship should end as one edge citing both."""
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")
    director = await person(repository, "A. Director")
    first, _ = await repository.assert_relationship(
        predicate="DIRECTOR_OF", subject_id=director.id, object_id=survivor.id
    )
    second, _ = await repository.assert_relationship(
        predicate="DIRECTOR_OF", subject_id=director.id, object_id=absorbed.id
    )

    result, _ = await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    neighbourhood = await repository.expand([survivor.id])
    edges = [r for r in neighbourhood.relationships if r.type == "DIRECTOR_OF"]
    assert result.edges_collapsed == 1
    assert len(edges) == 1
    assert {first.id, second.id} <= set(edges[0].assertion_ids)


async def test_an_edge_between_the_two_is_dropped(repository, merger) -> None:
    """It would become a loop: the company cannot own itself."""
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")
    await repository.assert_relationship(
        predicate="OWNS", subject_id=survivor.id, object_id=absorbed.id
    )

    result, _ = await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    neighbourhood = await repository.expand([survivor.id])
    assert result.self_edges_dropped == 1
    assert neighbourhood.relationships == []


async def test_the_absorbed_node_is_kept_but_hidden(repository, merger) -> None:
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")

    await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    assert await repository.get_entity(absorbed.id) is None, "hidden from queries"
    still_there = await repository.get_entity(absorbed.id, include_suppressed=True)
    assert still_there is not None, "kept, so the merge can be undone"
    assert still_there.properties["merged_into"] == survivor.id


async def test_provenance_follows_the_merge(repository, merger, neo4j_driver) -> None:
    """An assertion left pointing at a hidden node would strand the evidence."""
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")
    address = await repository.upsert_entity(
        EntityRecord(type="Address", properties={"name": "1 Main St"})
    )
    assertion, _ = await repository.assert_relationship(
        predicate="REGISTERED_AT", subject_id=absorbed.id, object_id=address.id
    )

    await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    claims = await repository.assertions_for(survivor.id, "REGISTERED_AT", address.id)
    assert [c.id for c in claims] == [assertion.id]


# --- undo -------------------------------------------------------------------


async def test_a_merge_can_be_undone(repository, merger) -> None:
    survivor = await company(repository, "Acme AS", jurisdiction="NO")
    absorbed = await company(repository, "ACME A/S", registration_number="912345678")
    address = await repository.upsert_entity(
        EntityRecord(type="Address", properties={"name": "1 Main St"})
    )
    await repository.assert_relationship(
        predicate="REGISTERED_AT", subject_id=absorbed.id, object_id=address.id
    )
    _, snapshot = await merger.merge(survivor.id, absorbed.id, merge_id=new_id())

    await merger.unmerge(snapshot)

    restored = await repository.get_entity(absorbed.id)
    kept = await repository.get_entity(survivor.id)
    assert restored is not None, "visible again"
    assert restored.properties.get("merged_into") is None
    assert "registration_number" not in kept.properties, "the survivor gave back what it took"
    absorbed_again = await repository.expand([absorbed.id])
    assert address.id in {e.id for e in absorbed_again.entities}, "its edge came back"


async def test_the_survivors_name_becomes_the_alias_when_it_loses(repository, merger) -> None:
    """Whichever name loses is kept, not just the absorbed record's.

    Choosing the absorbed record's name used to discard the survivor's
    entirely, losing the spelling a source had used.
    """
    survivor = await company(repository, "Acme AS")
    absorbed = await company(repository, "ACME A/S")

    await merger.merge(
        survivor.id, absorbed.id, resolutions={"name": "ACME A/S"}, merge_id=new_id()
    )

    kept = await repository.get_entity(survivor.id)
    assert kept.properties["name"] == "ACME A/S"
    assert "Acme AS" in kept.properties["aliases"], "the displaced name survives"
