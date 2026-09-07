"""Loading and validating the ontology.

Most of these exercise the failure paths: the loader's job is to reject an
incoherent ontology before any generator tries to build on it.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sla.ontology import load
from sla.ontology.loader import OntologyError

MINIMAL = """
version: 0.1.0
name: Test
entity_types:
  Thing:
    label: Thing
    abstract: true
    properties:
      name: {type: string, required: true}
  Person:
    label: Person
    extends: Thing
  Company:
    label: Company
    extends: Thing
relationship_types:
  OWNS:
    label: owns
    source: [Person]
    target: [Company]
"""


def write(tmp_path: Path, yaml_text: str) -> Path:
    path = tmp_path / "ontology.yaml"
    path.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    return path


# --- the real ontology ---------------------------------------------------


def test_shipped_ontology_is_valid() -> None:
    """The ontology in the repository must always load."""
    ontology = load()
    assert ontology.concrete_entity_types
    assert ontology.relationship_types


def test_inheritance_folds_in_supertype_properties() -> None:
    company = load().entity_type("Company")

    assert company.ancestors == ("LegalEntity", "Thing")
    assert company.labels == ("Company", "LegalEntity", "Thing")
    # own, from LegalEntity, and from Thing respectively
    assert {"legal_form", "registration_number", "name"} <= set(company.properties)


def test_abstract_types_expand_to_concrete_subtypes() -> None:
    ontology = load()

    assert ontology.concrete_subtypes("LegalEntity") == ("Company", "Organisation")
    assert "Document" in ontology.concrete_subtypes("Source")
    assert "LegalEntity" not in ontology.concrete_entity_types


def test_relationships_are_filtered_by_entity_type() -> None:
    """This is what keeps the right-click menu from offering nonsense."""
    ontology = load()

    assert "DIRECTOR_OF" in ontology.relationships_from("Person")
    assert "ISSUED_TENDER" not in ontology.relationships_from("Person")
    assert "ISSUED_TENDER" in ontology.relationships_from("Company")


def test_every_identifier_property_exists_on_its_type() -> None:
    ontology = load()

    for name, resolved in ontology.entity_types.items():
        for identifier in resolved.spec.identifiers:
            assert identifier in resolved.properties, f"{name}.{identifier}"


# --- rejection of incoherent ontologies ----------------------------------


def test_unknown_supertype_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path, MINIMAL.replace("extends: Thing\n  Company", "extends: Ghost\n  Company")
    )

    with pytest.raises(OntologyError, match="extends 'Ghost'"):
        load(path)


def test_inheritance_cycle_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        version: 0.1.0
        name: Test
        entity_types:
          A: {label: A, extends: B}
          B: {label: B, extends: A}
        """,
    )

    with pytest.raises(OntologyError, match="inheritance cycle"):
        load(path)


def test_relationship_endpoint_must_be_a_known_type(tmp_path: Path) -> None:
    path = write(tmp_path, MINIMAL.replace("target: [Company]", "target: [Vehicle]"))

    with pytest.raises(OntologyError, match="entity type 'Vehicle', which is not defined"):
        load(path)


def test_identifier_must_name_a_real_property(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        MINIMAL.replace(
            "    extends: Thing\n  Company",
            "    extends: Thing\n    identifiers: [tax_id]\n  Company",
        ),
    )

    with pytest.raises(OntologyError, match="identifiers names 'tax_id'"):
        load(path)


def test_display_template_must_name_real_properties(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        MINIMAL.replace(
            "  Company:\n    label: Company\n    extends: Thing",
            "  Company:\n    label: Company\n    extends: Thing\n"
            '    display_name: "{trading_name}"',
        ),
    )

    with pytest.raises(OntologyError, match=r"display_name refers to \{trading_name\}"):
        load(path)


def test_reserved_properties_cannot_be_redeclared(tmp_path: Path) -> None:
    """``id`` and friends are supplied by the system, not the ontology."""
    path = write(
        tmp_path,
        MINIMAL.replace(
            "      name: {type: string, required: true}",
            "      name: {type: string, required: true}\n      id: {type: string}",
        ),
    )

    with pytest.raises(OntologyError, match="reserved propert"):
        load(path)


def test_temporal_relationship_may_not_declare_validity_dates(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        MINIMAL.replace(
            "    target: [Company]",
            "    target: [Company]\n    properties:\n      valid_from: {type: date}",
        ),
    )

    with pytest.raises(OntologyError, match="supplied automatically"):
        load(path)


def test_redefining_an_inherited_property_with_a_new_type_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        MINIMAL.replace(
            "  Person:\n    label: Person\n    extends: Thing",
            "  Person:\n    label: Person\n    extends: Thing\n"
            "    properties:\n      name: {type: integer}",
        ),
    )

    with pytest.raises(OntologyError, match="different type"):
        load(path)


def test_enum_property_must_list_values(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        MINIMAL.replace(
            "      name: {type: string, required: true}",
            "      name: {type: string, required: true}\n      status: {type: enum}",
        ),
    )

    with pytest.raises(OntologyError, match="must list its allowed values"):
        load(path)


def test_symmetric_relationship_cannot_be_directed(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        MINIMAL.replace("    target: [Company]", "    target: [Company]\n    symmetric: true"),
    )

    with pytest.raises(OntologyError, match="symmetric relationship cannot also be directed"):
        load(path)


def test_missing_file_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(OntologyError, match="ontology file not found"):
        load(tmp_path / "nope.yaml")
