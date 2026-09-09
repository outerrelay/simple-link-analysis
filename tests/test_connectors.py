"""Parsing registry responses into ontology-conformant proposals.

The HTTP call and the parsing are separate so the parsing can be tested
without the network. The fixtures follow each API's documented response shape.

**These have not been checked against the live services from this project** —
outbound access to both is blocked here — so the fixtures are constructed from
the published formats rather than captured from real calls. The parsers are
written to tolerate missing fields for that reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sla.actions import companies_house, gleif
from sla.ontology import load

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ontology():
    return load()


def check_conforms(proposal, ontology) -> None:
    """Every proposed item must fit the ontology, or accepting it will fail."""
    by_id = {e.id: e for e in proposal.entities}
    for entity in proposal.entities:
        resolved = ontology.entity_type(entity.type)
        assert not resolved.abstract, f"{entity.type} is abstract"
        unknown = set(entity.properties) - set(resolved.properties)
        assert not unknown, f"{entity.type} has no properties {sorted(unknown)}"
    for relationship in proposal.relationships:
        spec = ontology.relationship_type(relationship.type)
        unknown = set(relationship.properties) - set(spec.properties)
        assert not unknown, f"{relationship.type} has no properties {sorted(unknown)}"
        for role, endpoint in (
            ("source", relationship.source_id),
            ("target", relationship.target_id),
        ):
            proposed = by_id.get(endpoint)
            if proposed is None:
                continue  # refers to an entity already in the graph
            permitted = {
                concrete
                for declared in getattr(spec, role)
                for concrete in ontology.concrete_subtypes(declared)
            }
            assert (
                proposed.type in permitted
            ), f"{relationship.type}.{role} does not accept {proposed.type}"


# --- GLEIF -----------------------------------------------------------------


def test_gleif_proposes_a_canonical_identifier(ontology) -> None:
    proposal = gleif.build_proposal("gleif.lookup", "subject-1", fixture("gleif_lei_record.json"))

    identifiers = [e for e in proposal.entities if e.type == "Identifier"]
    assert len(identifiers) == 1
    assert identifiers[0].properties["scheme"] == "lei"
    assert identifiers[0].properties["value"] == "213800LBQA1XJIQ7XZ44"
    check_conforms(proposal, ontology)


def test_gleif_links_the_identifier_to_the_subject(ontology) -> None:
    """This edge is what makes duplicate detection fire later."""
    proposal = gleif.build_proposal("gleif.lookup", "subject-1", fixture("gleif_lei_record.json"))

    edge = next(r for r in proposal.relationships if r.type == "HAS_IDENTIFIER")
    assert edge.source_id == "subject-1"


def test_gleif_proposes_the_registered_address(ontology) -> None:
    proposal = gleif.build_proposal("gleif.lookup", "subject-1", fixture("gleif_lei_record.json"))

    address = next(e for e in proposal.entities if e.type == "Address")
    assert address.properties["city"] == "London"
    assert address.properties["country"] == "GB"
    assert "1 Example Street" in address.properties["name"]


def test_gleif_handles_an_empty_result() -> None:
    proposal = gleif.build_proposal("gleif.lookup", "subject-1", {"data": []})

    assert not proposal
    assert "no LEI record" in proposal.summary


def test_gleif_tolerates_a_record_with_no_address(ontology) -> None:
    payload = {"data": [{"id": "X", "attributes": {"lei": "X", "entity": {}}}]}

    proposal = gleif.build_proposal("gleif.lookup", "subject-1", payload)

    assert [e.type for e in proposal.entities] == ["Identifier"]
    check_conforms(proposal, ontology)


# --- Companies House: profile ----------------------------------------------


def test_profile_updates_the_existing_company_rather_than_duplicating(ontology) -> None:
    proposal = companies_house.build_profile_proposal(
        "companies_house.profile", "subject-1", fixture("companies_house_profile.json")
    )

    company = next(e for e in proposal.entities if e.type == "Company")
    assert company.existing_id == "subject-1"
    assert company.target_id == "subject-1"
    check_conforms(proposal, ontology)


def test_profile_maps_status_onto_the_ontology_enum(ontology) -> None:
    proposal = companies_house.build_profile_proposal(
        "companies_house.profile", "subject-1", fixture("companies_house_profile.json")
    )

    company = next(e for e in proposal.entities if e.type == "Company")
    allowed = ontology.entity_type("Company").properties["status"].enum
    assert company.properties["status"] in allowed


def test_profile_proposes_the_registered_office(ontology) -> None:
    proposal = companies_house.build_profile_proposal(
        "companies_house.profile", "subject-1", fixture("companies_house_profile.json")
    )

    address = next(e for e in proposal.entities if e.type == "Address")
    edge = next(r for r in proposal.relationships if r.type == "REGISTERED_AT")
    assert address.properties["postal_code"] == "EC1A 1BB"
    assert edge.properties["address_type"] == "registered"


# --- Companies House: officers ---------------------------------------------


def test_officers_become_people_with_readable_names(ontology) -> None:
    proposal = companies_house.build_officers_proposal(
        "companies_house.officers", "subject-1", fixture("companies_house_officers.json")
    )

    names = [e.properties["name"] for e in proposal.entities]
    assert "Jane Elizabeth Smith" in names, "SURNAME, Firstname is reordered"
    check_conforms(proposal, ontology)


def test_appointment_dates_become_relationship_validity(ontology) -> None:
    """A resigned director is a relationship that has ended, not one that never was."""
    proposal = companies_house.build_officers_proposal(
        "companies_house.officers", "subject-1", fixture("companies_house_officers.json")
    )

    resigned = next(r for r in proposal.relationships if r.valid_to)
    assert resigned.valid_from == "2011-01-10"
    assert resigned.valid_to == "2019-03-31"


def test_a_secretary_is_an_officer_not_a_director(ontology) -> None:
    proposal = companies_house.build_officers_proposal(
        "companies_house.officers", "subject-1", fixture("companies_house_officers.json")
    )

    types = {r.type for r in proposal.relationships}
    assert "OFFICER_OF" in types
    assert types <= {"DIRECTOR_OF", "OFFICER_OF", "MEMBER_OF"}


def test_partial_dates_of_birth_are_dropped_rather_than_invented() -> None:
    """Companies House publishes only month and year.

    Recording the first of the month would assert a precision the register
    does not have.
    """
    proposal = companies_house.build_officers_proposal(
        "companies_house.officers", "subject-1", fixture("companies_house_officers.json")
    )

    assert all("birth_date" not in e.properties for e in proposal.entities)


def test_free_text_nationality_is_not_guessed_into_a_country_code() -> None:
    """ "British" is not an ISO code, and inventing "GB" would be fabrication."""
    proposal = companies_house.build_officers_proposal(
        "companies_house.officers", "subject-1", fixture("companies_house_officers.json")
    )

    assert all("nationality" not in e.properties for e in proposal.entities)


def test_officers_handles_an_empty_response() -> None:
    proposal = companies_house.build_officers_proposal(
        "companies_house.officers", "subject-1", {"items": []}
    )

    assert not proposal
    assert proposal.summary == "0 officers"


def test_officers_skips_an_item_with_no_name() -> None:
    proposal = companies_house.build_officers_proposal(
        "companies_house.officers", "subject-1", {"items": [{"officer_role": "director"}]}
    )

    assert proposal.entities == []
