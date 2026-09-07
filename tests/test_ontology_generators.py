"""Generated artefacts: determinism, correctness and staleness detection."""

from __future__ import annotations

import json

import pytest

from sla.ontology import load
from sla.ontology.generators import render_all
from sla.ontology.generators._common import BANNER


def rendered(suffix: str) -> str:
    """The rendered artefact whose path ends with ``suffix``."""
    artefacts = render_all(load())
    return next(content for path, content in artefacts.items() if path.suffix == suffix)


def test_generation_is_deterministic() -> None:
    """Byte-identical output is what makes the staleness check meaningful."""
    first = render_all(load())
    second = render_all(load())

    assert first == second


def test_committed_artefacts_match_the_ontology() -> None:
    """The repository equivalent of `sla-ontology check`.

    Generated files are committed so a fresh clone works; this is what stops
    them drifting from the ontology they came from.
    """
    from sla.ontology.cli import REPO_ROOT

    stale = [
        path
        for path, content in render_all(load()).items()
        if (REPO_ROOT / path).read_text(encoding="utf-8") != content
    ]

    assert not stale, f"run `sla-ontology generate`; stale: {stale}"


def test_every_artefact_says_it_is_generated() -> None:
    for content in render_all(load()).values():
        assert BANNER.splitlines()[0] in content


# --- Pydantic models -----------------------------------------------------


def test_generated_models_cover_every_concrete_type() -> None:
    from sla.ontology.generated.models import ENTITY_MODELS, RELATIONSHIP_MODELS

    ontology = load()

    assert set(ENTITY_MODELS) == set(ontology.concrete_entity_types)
    assert set(RELATIONSHIP_MODELS) == set(ontology.all_relationship_types)


def test_abstract_types_get_no_model() -> None:
    from sla.ontology.generated.models import ENTITY_MODELS

    assert "LegalEntity" not in ENTITY_MODELS
    assert "Thing" not in ENTITY_MODELS


def test_generated_model_enforces_required_properties() -> None:
    import pydantic

    from sla.ontology.generated.models import Company

    with pytest.raises(pydantic.ValidationError):
        Company(id="u1")  # 'name' is required by the ontology


def test_generated_model_rejects_properties_the_ontology_does_not_declare() -> None:
    import pydantic

    from sla.ontology.generated.models import Company

    with pytest.raises(pydantic.ValidationError):
        Company(id="u1", name="Acme", invented_field="x")


def test_temporal_relationships_carry_validity_dates() -> None:
    from sla.ontology.generated.models import Owns, Sent

    assert "valid_from" in Owns.model_fields
    # SENT is declared non-temporal: a payment happens, it does not persist.
    assert "valid_from" not in Sent.model_fields


def test_multi_valued_property_becomes_a_list() -> None:
    from sla.ontology.generated.models import Person

    person = Person(id="u1", name="A. Person", nationality=["NO", "DE"])

    assert person.nationality == ["NO", "DE"]


# --- JSON Schema ---------------------------------------------------------


def test_json_schema_is_valid_json_and_defines_every_type() -> None:
    ontology = load()
    schema = json.loads(rendered(".json"))

    for name in ontology.concrete_entity_types:
        assert name in schema["$defs"]
    assert schema["x-ontology-version"] == ontology.version


def test_json_schema_expands_abstract_endpoints_to_concrete_types() -> None:
    """A consumer should not have to resolve our inheritance itself."""
    schema = json.loads(rendered(".json"))

    owns = schema["$defs"]["OWNS"]

    assert "Company" in owns["x-target-types"]
    assert "LegalEntity" not in owns["x-target-types"]


def test_json_schema_marks_personal_data() -> None:
    schema = json.loads(rendered(".json"))

    assert schema["$defs"]["Person"]["properties"]["birth_date"]["x-pii"] is True


# --- Cypher --------------------------------------------------------------


def test_cypher_constrains_id_uniqueness_on_the_root_label() -> None:
    """One constraint on :Thing covers every node, since all carry that label."""
    cypher = rendered(".cypher")

    assert "FOR (n:Thing) REQUIRE n.id IS UNIQUE" in cypher


def test_cypher_makes_identifiers_canonical() -> None:
    """One node per (scheme, value), so two entities sharing an LEI share a node."""
    cypher = rendered(".cypher")

    assert "FOR (n:Identifier) REQUIRE (n.scheme, n.value) IS UNIQUE" in cypher


def test_cypher_statements_are_idempotent() -> None:
    """Applying the schema twice must not fail."""
    cypher = rendered(".cypher")

    statements = [s for s in cypher.split(";") if s.strip() and not s.strip().startswith("//")]
    assert statements
    for statement in statements:
        assert "IF NOT EXISTS" in statement
