"""Actions and the staging path into the graph.

The invariant under test throughout: an action never writes to Neo4j. Only
accepting a proposal does.
"""

from __future__ import annotations

import pytest

from sla.actions import registry, runner
from sla.actions.base import ActionContext, ActionError
from sla.actions.expand import ExpandFromDatabase
from sla.app import database, staging
from sla.app.staging import Proposal, ProposedEntity, ProposedRelationship
from sla.config import Settings, WritePolicy
from sla.graph.model import EntityRecord


@pytest.fixture(autouse=True)
def app_db(tmp_path):
    database.init(Settings(_env_file=None, app_database_url=f"sqlite:///{tmp_path}/app.sqlite"))
    yield
    database.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


class FakeAction:
    """An action that proposes a fixed company, for testing the machinery."""

    id = "test.fake"
    label = "Fake action"
    description = "Proposes one company."
    input_types = ("Person",)
    output_types = ("Company",)
    default_policy = WritePolicy.REVIEW
    requires: tuple[str, ...] = ()

    def __init__(self, policy: WritePolicy = WritePolicy.REVIEW) -> None:
        self.default_policy = policy

    async def run(self, context: ActionContext) -> Proposal:
        company = ProposedEntity(
            type="Company",
            id="prov-company",
            properties={"name": "Proposed AS", "registration_number": "999888777"},
        )
        return Proposal(
            action_id=self.id,
            entities=[company],
            relationships=[
                ProposedRelationship(type="OWNS", source_id=context.entity.id, target_id=company.id)
            ],
            summary="1 company",
        )


class NeedsCredential:
    id = "test.credential"
    label = "Needs a key"
    description = ""
    input_types = ("Person",)
    output_types = ()
    default_policy = WritePolicy.REVIEW
    requires = ("companies_house_api_key",)

    async def run(self, context: ActionContext) -> Proposal:  # pragma: no cover
        raise AssertionError("should never run without its credential")


async def a_person(repository) -> EntityRecord:
    return await repository.upsert_entity(
        EntityRecord(type="Person", properties={"name": "A. Subject"})
    )


# --- the core invariant ----------------------------------------------------


async def test_running_an_action_writes_nothing_to_the_graph(
    repository, ontology, settings
) -> None:
    """The whole point of staging."""
    person = await a_person(repository)

    run = await runner.run_action(
        FakeAction(),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )

    assert run.accepted is None
    assert len(run.staged.entities) == 1
    assert await repository.find_entities(entity_type="Company") == []


async def test_accepting_writes_to_the_graph(repository, ontology, settings) -> None:
    person = await a_person(repository)
    run = await runner.run_action(
        FakeAction(),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )

    result = await runner.accept(
        run.staged.id,
        item_ids=[item.id for item in run.staged.items],
        rejected_ids=[],
        repository=repository,
    )

    companies = await repository.find_entities(entity_type="Company")
    assert result.entities_written == 1
    assert result.relationships_written == 1
    assert [c.properties["name"] for c in companies] == ["Proposed AS"]


async def test_rejecting_writes_nothing_and_is_remembered(repository, ontology, settings) -> None:
    person = await a_person(repository)
    run = await runner.run_action(
        FakeAction(),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )

    await runner.reject_all(run.staged.id)

    assert await repository.find_entities(entity_type="Company") == []

    second = await runner.run_action(
        FakeAction(),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )
    assert second.staged.items == [], "the refusal should stop it being proposed again"


async def test_partial_acceptance(repository, ontology, settings) -> None:
    """Accept the company but not the ownership claim."""
    person = await a_person(repository)
    run = await runner.run_action(
        FakeAction(),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )

    result = await runner.accept(
        run.staged.id,
        item_ids=[run.staged.entities[0].id],
        rejected_ids=[run.staged.relationships[0].id],
        repository=repository,
    )

    assert result.entities_written == 1
    assert result.relationships_written == 0
    neighbourhood = await repository.expand([person.id])
    assert neighbourhood.relationships == []


# --- write policy ----------------------------------------------------------


async def test_auto_commit_writes_immediately(repository, ontology, settings) -> None:
    person = await a_person(repository)

    run = await runner.run_action(
        FakeAction(policy=WritePolicy.AUTO_COMMIT),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )

    assert run.accepted is not None
    assert run.accepted.entities_written == 1
    assert len(await repository.find_entities(entity_type="Company")) == 1
    assert staging.get_set(run.staged.id).status == "accepted"


async def test_per_invocation_policy_beats_the_action_default(
    repository, ontology, settings
) -> None:
    """An action that normally commits can be asked to preview instead."""
    person = await a_person(repository)

    run = await runner.run_action(
        FakeAction(policy=WritePolicy.AUTO_COMMIT),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
        policy=WritePolicy.REVIEW,
    )

    assert run.accepted is None
    assert await repository.find_entities(entity_type="Company") == []


def test_policy_resolution_order(settings) -> None:
    action = FakeAction(policy=WritePolicy.REVIEW)

    assert runner.resolve_policy(action, settings) is WritePolicy.REVIEW
    assert (
        runner.resolve_policy(action, settings, WritePolicy.AUTO_COMMIT) is WritePolicy.AUTO_COMMIT
    )


# --- guards ----------------------------------------------------------------


async def test_an_action_refuses_a_type_it_does_not_accept(repository, ontology, settings) -> None:
    company = await repository.upsert_entity(
        EntityRecord(type="Company", properties={"name": "Acme AS"})
    )

    with pytest.raises(ActionError, match="does not apply"):
        await runner.run_action(
            FakeAction(),
            entity_id=company.id,
            repository=repository,
            ontology=ontology,
            settings=settings,
        )


async def test_a_missing_credential_stops_the_action(repository, ontology, settings) -> None:
    person = await a_person(repository)

    with pytest.raises(ActionError, match="companies_house_api_key"):
        await runner.run_action(
            NeedsCredential(),
            entity_id=person.id,
            repository=repository,
            ontology=ontology,
            settings=settings,
        )


async def test_a_missing_entity_stops_the_action(repository, ontology, settings) -> None:
    with pytest.raises(ActionError, match="no entity"):
        await runner.run_action(
            FakeAction(),
            entity_id="no-such-id",
            repository=repository,
            ontology=ontology,
            settings=settings,
        )


async def test_one_bad_item_does_not_lose_the_good_ones(
    repository, ontology, settings, monkeypatch
) -> None:
    """Eleven good officers should survive one malformed twelfth."""
    person = await a_person(repository)

    class MixedAction(FakeAction):
        async def run(self, context):
            good = ProposedEntity(type="Company", id="good", properties={"name": "Good AS"})
            bad = ProposedEntity(
                type="Company", id="bad", properties={"name": "Bad AS", "nonsense": 1}
            )
            return Proposal(action_id=self.id, entities=[good, bad], summary="2")

    run = await runner.run_action(
        MixedAction(),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )
    result = await runner.accept(
        run.staged.id,
        item_ids=[item.id for item in run.staged.items],
        rejected_ids=[],
        repository=repository,
    )

    written = await repository.find_entities(entity_type="Company")
    assert result.entities_written == 1
    assert len(result.errors) == 1
    assert [c.properties["name"] for c in written] == ["Good AS"]


# --- expand from the database ----------------------------------------------


async def test_expand_action_proposes_what_the_graph_already_holds(
    repository, ontology, settings
) -> None:
    person = await a_person(repository)
    company = await repository.upsert_entity(
        EntityRecord(type="Company", properties={"name": "Acme AS"})
    )
    await repository.assert_relationship(
        predicate="OWNS", subject_id=person.id, object_id=company.id
    )

    run = await runner.run_action(
        ExpandFromDatabase(),
        entity_id=person.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
    )

    assert run.accepted is not None, "expansion auto-commits; the data is already stored"
    assert [i.payload["properties"]["name"] for i in run.staged.entities] == ["Acme AS"]


# --- registry --------------------------------------------------------------


def test_registry_offers_actions_by_entity_type(ontology, settings) -> None:
    """Input types resolve through the ontology's inheritance."""
    for_company = {a.id for a in registry.applicable_to("Company", ontology, settings)}
    for_person = {a.id for a in registry.applicable_to("Person", ontology, settings)}

    assert "gleif.lookup" in for_company, "declared on LegalEntity, so a Company qualifies"
    assert "gleif.lookup" not in for_person
    assert "expand.database" in for_person, "declared on Thing, so everything qualifies"


def test_actions_without_credentials_are_listed_but_marked_unavailable(ontology, settings) -> None:
    """Hiding them would leave the user wondering where the lookup went."""
    descriptors = {a.id: a for a in registry.applicable_to("Company", ontology, settings)}
    profile = descriptors["companies_house.profile"]

    assert profile.available is False
    assert "companies_house_api_key" in profile.unavailable_reason


def test_registering_a_duplicate_id_is_refused() -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ExpandFromDatabase())


# --- values that are not strings -------------------------------------------


async def test_dates_survive_the_round_trip_through_staging(repository, ontology, settings) -> None:
    """Staging is JSON, so dates go out as ISO strings and must come back typed.

    Storing them as text in Neo4j would quietly break every temporal query,
    which is the sort of failure that shows up months later.
    """
    from datetime import date

    person = await repository.upsert_entity(
        EntityRecord(
            type="Person",
            properties={"name": "A. Person", "birth_date": date(1968, 4, 12)},
        )
    )
    company = await repository.upsert_entity(
        EntityRecord(type="Company", properties={"name": "Acme AS"})
    )
    await repository.assert_relationship(
        predicate="DIRECTOR_OF",
        subject_id=person.id,
        object_id=company.id,
        valid_from=date(2019, 3, 1),
    )

    # Expanding produces a proposal carrying those real date objects.
    run = await runner.run_action(
        ExpandFromDatabase(),
        entity_id=company.id,
        repository=repository,
        ontology=ontology,
        settings=settings,
        policy=WritePolicy.REVIEW,
    )
    payload = next(item.payload for item in run.staged.entities if item.payload["type"] == "Person")
    assert payload["properties"]["birth_date"] == "1968-04-12", "stored as an ISO string"

    await runner.accept(
        run.staged.id,
        item_ids=[item.id for item in run.staged.items],
        rejected_ids=[],
        repository=repository,
    )

    stored = await repository.get_entity(person.id)
    assert stored.properties["birth_date"] == date(1968, 4, 12), "typed again on the way in"

    as_of = await repository.expand([company.id], as_of=date(2020, 1, 1))
    assert len(as_of.relationships) == 1, "the temporal query still works"


async def test_an_unparseable_date_is_reported_not_stored(repository) -> None:
    with pytest.raises(Exception, match="cannot read"):
        await repository.upsert_entity(
            EntityRecord(
                type="Person", properties={"name": "A. Person", "birth_date": "not-a-date"}
            )
        )


async def test_a_value_outside_an_enum_is_refused(repository) -> None:
    with pytest.raises(Exception, match="not one of"):
        await repository.upsert_entity(
            EntityRecord(type="Company", properties={"name": "Acme AS", "status": "invented"})
        )
