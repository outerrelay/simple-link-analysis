"""Classifying ontology changes as safe or requiring a migration."""

from __future__ import annotations

import textwrap
from pathlib import Path

from sla.ontology import load
from sla.ontology.diff import Impact, diff

BASE = """
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
    properties:
      birth_date: {type: date}
      status: {type: enum, enum: [active, inactive]}
  Company:
    label: Company
    extends: Thing
relationship_types:
  OWNS:
    label: owns
    source: [Person, Company]
    target: [Company]
"""


def build(tmp_path: Path, yaml_text: str, name: str):
    path = tmp_path / f"{name}.yaml"
    path.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    return load(path)


def changes_for(tmp_path: Path, replacement: tuple[str, str]):
    old = build(tmp_path, BASE, "old")
    new = build(tmp_path, BASE.replace(*replacement), "new")
    return diff(old, new)


def test_identical_ontologies_produce_no_changes(tmp_path: Path) -> None:
    result = diff(build(tmp_path, BASE, "a"), build(tmp_path, BASE, "b"))

    assert not result
    assert result.is_safe


# --- additive ------------------------------------------------------------


def test_new_optional_property_is_additive(tmp_path: Path) -> None:
    result = changes_for(
        tmp_path,
        (
            "      birth_date: {type: date}",
            "      birth_date: {type: date}\n      nickname: {type: string}",
        ),
    )

    assert result.is_safe
    assert any(c.subject == "Person.nickname" for c in result.additive)


def test_new_entity_type_is_additive(tmp_path: Path) -> None:
    result = changes_for(
        tmp_path,
        (
            "relationship_types:",
            "  Vessel:\n    label: Vessel\n    extends: Thing\nrelationship_types:",
        ),
    )

    assert result.is_safe
    assert any(c.subject == "Vessel" for c in result.additive)


def test_added_enum_value_is_additive(tmp_path: Path) -> None:
    result = changes_for(
        tmp_path, ("enum: [active, inactive]", "enum: [active, inactive, pending]")
    )

    assert result.is_safe


def test_widening_a_relationship_endpoint_is_additive(tmp_path: Path) -> None:
    result = changes_for(tmp_path, ("    target: [Company]", "    target: [Company, Person]"))

    assert result.is_safe
    assert any("now also accepts" in c.description for c in result.additive)


# --- breaking ------------------------------------------------------------


def test_removed_property_is_breaking_and_suggests_the_rename_case(tmp_path: Path) -> None:
    """A rename reads as a removal plus an addition, so the note says so."""
    result = changes_for(
        tmp_path, ("      birth_date: {type: date}", "      date_of_birth: {type: date}")
    )

    assert not result.is_safe
    removed = next(c for c in result.breaking if c.subject == "Person.birth_date")
    assert "rename" in removed.migration
    assert any(c.subject == "Person.date_of_birth" for c in result.additive)


def test_making_a_property_required_is_breaking(tmp_path: Path) -> None:
    result = changes_for(
        tmp_path,
        ("      birth_date: {type: date}", "      birth_date: {type: date, required: true}"),
    )

    assert not result.is_safe
    assert "Backfill" in next(iter(result.breaking)).migration


def test_changing_a_property_type_is_breaking(tmp_path: Path) -> None:
    result = changes_for(
        tmp_path, ("      birth_date: {type: date}", "      birth_date: {type: string}")
    )

    assert not result.is_safe
    assert any("type changed" in c.description for c in result.breaking)


def test_changing_cardinality_is_breaking(tmp_path: Path) -> None:
    result = changes_for(
        tmp_path, ("      birth_date: {type: date}", "      birth_date: {type: date, multi: true}")
    )

    assert not result.is_safe
    assert any("cardinality" in c.description for c in result.breaking)


def test_removing_an_enum_value_is_breaking(tmp_path: Path) -> None:
    result = changes_for(tmp_path, ("enum: [active, inactive]", "enum: [active]"))

    assert not result.is_safe
    assert any("enum values removed" in c.description for c in result.breaking)


def test_narrowing_a_relationship_endpoint_is_breaking(tmp_path: Path) -> None:
    result = changes_for(tmp_path, ("    source: [Person, Company]", "    source: [Company]"))

    assert not result.is_safe
    narrowed = next(c for c in result.breaking if c.subject == "OWNS.source")
    assert "Person" in narrowed.migration


def test_removing_an_entity_type_is_breaking(tmp_path: Path) -> None:
    base = build(tmp_path, BASE, "old")
    trimmed = BASE.replace("  Company:\n    label: Company\n    extends: Thing\n", "").replace(
        "    source: [Person, Company]\n    target: [Company]",
        "    source: [Person]\n    target: [Person]",
    )
    result = diff(base, build(tmp_path, trimmed, "new"))

    assert not result.is_safe
    assert any(c.subject == "Company" and c.impact is Impact.BREAKING for c in result.breaking)


def test_new_required_property_is_breaking(tmp_path: Path) -> None:
    result = changes_for(
        tmp_path,
        (
            "      birth_date: {type: date}",
            "      birth_date: {type: date}\n      country: {type: country, required: true}",
        ),
    )

    assert not result.is_safe
    assert any(c.subject == "Person.country" for c in result.breaking)
