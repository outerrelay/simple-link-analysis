"""Generators turning the ontology into artefacts other code consumes.

Each generator is a pure function from an :class:`~sla.ontology.loader.Ontology`
to file contents, and must be **deterministic** — the same ontology always
produces byte-identical output. That is what lets ``sla-ontology check`` detect
a generated file that has fallen behind the ontology it came from.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sla.ontology.generators import cypher, json_schema, pydantic_models
from sla.ontology.loader import Ontology

# Output path (relative to the repository root) -> renderer.
GENERATORS: dict[str, Callable[[Ontology], str]] = {
    "src/sla/ontology/generated/models.py": pydantic_models.render,
    "ontology/build/ontology.schema.json": json_schema.render,
    "ontology/build/constraints.cypher": cypher.render,
}


def render_all(ontology: Ontology) -> dict[Path, str]:
    """Render every artefact, keyed by its path relative to the repository root."""
    return {Path(path): render(ontology) for path, render in GENERATORS.items()}


__all__ = ["GENERATORS", "render_all", "cypher", "json_schema", "pydantic_models"]
