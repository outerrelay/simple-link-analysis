"""Proposals, fingerprints and tombstones."""

from __future__ import annotations

import pytest

from sla.app import database, staging
from sla.app.staging import Proposal, ProposedEntity, ProposedRelationship
from sla.config import Settings


@pytest.fixture(autouse=True)
def app_db(tmp_path):
    database.init(Settings(_env_file=None, app_database_url=f"sqlite:///{tmp_path}/app.sqlite"))
    yield
    database.dispose()


def company(name: str, number: str | None = None, entity_id: str = "prov-1") -> ProposedEntity:
    properties = {"name": name}
    if number:
        properties["registration_number"] = number
    return ProposedEntity(type="Company", properties=properties, id=entity_id)


def test_recording_a_proposal_keeps_its_items() -> None:
    proposal = Proposal(
        action_id="test",
        entities=[company("Acme AS", "912345678")],
        relationships=[ProposedRelationship(type="OWNS", source_id="a", target_id="prov-1")],
        summary="1 company",
    )

    staged = staging.record(proposal)

    assert staged.status == "pending"
    assert len(staged.entities) == 1
    assert len(staged.relationships) == 1
    assert staged.summary == "1 company"


# --- fingerprints ---------------------------------------------------------


def test_fingerprint_survives_a_new_provisional_id() -> None:
    """The same claim from a second run must match the first.

    Provisional UUIDs differ every run, so a fingerprint over the id would
    never match and a rejection could never be remembered.
    """
    first = staging.entity_fingerprint(company("Acme AS", "912345678", entity_id="a"))
    second = staging.entity_fingerprint(company("Acme AS", "912345678", entity_id="b"))

    assert first == second


def test_fingerprint_distinguishes_different_companies() -> None:
    assert staging.entity_fingerprint(company("Acme AS", "912345678")) != (
        staging.entity_fingerprint(company("Beta AS", "987654321"))
    )


def test_fingerprint_prefers_strong_identifiers_over_the_name() -> None:
    """A renamed company with the same number is the same claim."""
    renamed = company("Acme Holdings AS", "912345678")
    original = company("Acme AS", "912345678")

    assert staging.entity_fingerprint(renamed) == staging.entity_fingerprint(original)


def test_relationship_fingerprint_resolves_endpoints_that_are_also_proposed() -> None:
    entity = company("Acme AS", "912345678", entity_id="prov-1")
    relationship = ProposedRelationship(type="OWNS", source_id="stable", target_id="prov-1")

    first = staging.relationship_fingerprint(relationship, {"prov-1": entity})
    renumbered = company("Acme AS", "912345678", entity_id="prov-999")
    second = staging.relationship_fingerprint(
        ProposedRelationship(type="OWNS", source_id="stable", target_id="prov-999"),
        {"prov-999": renumbered},
    )

    assert first == second


# --- tombstones ------------------------------------------------------------


def test_rejecting_an_item_stops_it_being_proposed_again() -> None:
    """The behaviour the whole fingerprint machinery exists for."""
    proposal = Proposal(action_id="test", entities=[company("Acme AS", "912345678")])
    staged = staging.record(proposal)
    staging.decide_items(staged.id, accepted=[], rejected=[staged.entities[0].id])

    again = staging.record(
        Proposal(action_id="test", entities=[company("Acme AS", "912345678", entity_id="new")])
    )

    assert again.entities == [], "already refused, so not offered again"


def test_a_rejected_endpoint_takes_its_relationships_with_it() -> None:
    """An edge to something refused has nothing to attach to."""
    staged = staging.record(
        Proposal(
            action_id="test",
            entities=[company("Acme AS", "912345678")],
            relationships=[
                ProposedRelationship(type="OWNS", source_id="stable", target_id="prov-1")
            ],
        )
    )
    staging.decide_items(staged.id, accepted=[], rejected=[staged.entities[0].id])

    again = staging.record(
        Proposal(
            action_id="test",
            entities=[company("Acme AS", "912345678")],
            relationships=[
                ProposedRelationship(type="OWNS", source_id="stable", target_id="prov-1")
            ],
        )
    )

    assert again.entities == []
    assert again.relationships == []


def test_accepting_does_not_tombstone() -> None:
    staged = staging.record(Proposal(action_id="test", entities=[company("Acme AS", "912345678")]))

    staging.decide_items(staged.id, accepted=[staged.entities[0].id], rejected=[])

    assert staging.rejected_fingerprints() == set()


def test_a_rejection_can_be_forgotten() -> None:
    staged = staging.record(Proposal(action_id="test", entities=[company("Acme AS", "912345678")]))
    staging.decide_items(staged.id, accepted=[], rejected=[staged.entities[0].id])
    fingerprint = next(iter(staging.rejected_fingerprints()))

    assert staging.clear_rejection(fingerprint) is True

    again = staging.record(Proposal(action_id="test", entities=[company("Acme AS", "912345678")]))
    assert len(again.entities) == 1


# --- set status ------------------------------------------------------------


def test_status_reflects_a_mixed_decision() -> None:
    """Accepting eight of twelve officers is a normal outcome, not an error."""
    staged = staging.record(
        Proposal(
            action_id="test",
            entities=[
                company("A AS", "1", entity_id="a"),
                company("B AS", "2", entity_id="b"),
            ],
        )
    )
    ids = [item.id for item in staged.entities]

    updated = staging.decide_items(staged.id, accepted=[ids[0]], rejected=[ids[1]])

    assert updated.status == "partial"


def test_status_is_accepted_when_everything_is() -> None:
    staged = staging.record(
        Proposal(action_id="test", entities=[company("A AS", "1", entity_id="a")])
    )

    updated = staging.decide_items(staged.id, accepted=[staged.entities[0].id], rejected=[])

    assert updated.status == "accepted"


def test_a_set_stays_pending_until_every_item_is_decided() -> None:
    staged = staging.record(
        Proposal(
            action_id="test",
            entities=[
                company("A AS", "1", entity_id="a"),
                company("B AS", "2", entity_id="b"),
            ],
        )
    )

    updated = staging.decide_items(staged.id, accepted=[staged.entities[0].id], rejected=[])

    assert updated.status == "pending"


def test_pending_sets_exclude_decided_ones() -> None:
    first = staging.record(
        Proposal(action_id="test", entities=[company("A AS", "1", entity_id="a")])
    )
    staging.record(Proposal(action_id="test", entities=[company("B AS", "2", entity_id="b")]))
    staging.decide_items(first.id, accepted=[first.entities[0].id], rejected=[])

    assert [s.id for s in staging.pending_sets()] != [first.id]
    assert len(staging.pending_sets()) == 1


# --- jobs ------------------------------------------------------------------


def test_job_lifecycle() -> None:
    job_id = staging.create_job("test.action", subject_entity_id="e1", chart_id=None)

    assert staging.get_job(job_id).status == "running"

    staging.finish_job(job_id, status="succeeded", message="done", proposal_set_id="ps1")
    job = staging.get_job(job_id)

    assert job.status == "succeeded"
    assert job.proposal_set_id == "ps1"
