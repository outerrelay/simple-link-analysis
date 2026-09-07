"""Command line for working with the ontology.

sla-ontology validate            # is the ontology coherent?
sla-ontology generate            # rewrite the generated artefacts
sla-ontology check               # are the artefacts up to date? (CI)
sla-ontology diff OLD [NEW]      # what changed, and is it safe to apply?
sla-ontology show [TYPE]         # summarise the ontology
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from sla.ontology.diff import diff
from sla.ontology.generators import render_all
from sla.ontology.loader import DEFAULT_ONTOLOGY_PATH, Ontology, OntologyError, load

REPO_ROOT = DEFAULT_ONTOLOGY_PATH.parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except OntologyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sla-ontology", description=__doc__)
    parser.add_argument(
        "--ontology",
        type=Path,
        default=DEFAULT_ONTOLOGY_PATH,
        help="path to the ontology file (default: ontology/ontology.yaml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="check the ontology is coherent")
    validate.set_defaults(handler=_validate)

    generate = subparsers.add_parser("generate", help="rewrite the generated artefacts")
    generate.set_defaults(handler=_generate)

    check = subparsers.add_parser("check", help="fail if any generated artefact is out of date")
    check.set_defaults(handler=_check)

    diff_parser = subparsers.add_parser(
        "diff", help="classify the changes between two ontology versions"
    )
    diff_parser.add_argument(
        "old",
        help="the earlier ontology: a file path, or 'git:REF' to read it from git",
    )
    diff_parser.add_argument("new", nargs="?", help="the later ontology (default: --ontology)")
    diff_parser.set_defaults(handler=_diff)

    show = subparsers.add_parser("show", help="summarise the ontology")
    show.add_argument("type", nargs="?", help="an entity type to describe in detail")
    show.set_defaults(handler=_show)

    return parser


def _validate(args: argparse.Namespace) -> int:
    ontology = load(args.ontology)
    print(
        f"{args.ontology}: valid — "
        f"{len(ontology.concrete_entity_types)} concrete entity types "
        f"({len(ontology.entity_types)} including abstract), "
        f"{len(ontology.relationship_types)} relationship types, "
        f"{len(ontology.system_relationship_types)} system relationship types"
    )
    return 0


def _generate(args: argparse.Namespace) -> int:
    ontology = load(args.ontology)
    for relative_path, content in sorted(render_all(ontology).items()):
        destination = REPO_ROOT / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        unchanged = destination.exists() and destination.read_text(encoding="utf-8") == content
        destination.write_text(content, encoding="utf-8")
        print(f"{'unchanged' if unchanged else 'wrote    '}  {relative_path}")
    return 0


def _check(args: argparse.Namespace) -> int:
    """Fail when a generated file no longer matches the ontology.

    Generated artefacts are committed so the repository works straight after a
    clone; this is what stops them silently drifting from their source.
    """
    ontology = load(args.ontology)
    stale: list[Path] = []
    for relative_path, content in sorted(render_all(ontology).items()):
        destination = REPO_ROOT / relative_path
        if not destination.exists() or destination.read_text(encoding="utf-8") != content:
            stale.append(relative_path)

    if stale:
        print("generated artefacts are out of date:", file=sys.stderr)
        for path in stale:
            print(f"  - {path}", file=sys.stderr)
        print("\nrun: sla-ontology generate", file=sys.stderr)
        return 1

    print("generated artefacts are up to date")
    return 0


def _diff(args: argparse.Namespace) -> int:
    old = _load_reference(args.old)
    new = load(args.new or args.ontology)
    result = diff(old, new)

    if not result:
        print(f"no changes between {result.old_version} and {result.new_version}")
        return 0

    print(f"ontology {result.old_version} -> {result.new_version}\n")

    if result.additive:
        print(f"Additive ({len(result.additive)}) — safe to apply to existing data:")
        for change in result.additive:
            print(f"  + {change.subject}: {change.description}")
        print()

    if result.breaking:
        print(f"Breaking ({len(result.breaking)}) — existing data needs migrating:")
        for change in result.breaking:
            print(f"  ! {change.subject}: {change.description}")
            if change.migration:
                print(f"      migration: {change.migration}")
        print()
        print(
            "Regenerating code will NOT update data already in Neo4j. Write a "
            "migration for the changes above before applying this ontology."
        )
        return 1

    print("All changes are additive; regenerate and apply.")
    return 0


def _load_reference(reference: str) -> Ontology:
    """Load an ontology from a path, or from git with ``git:REF`` syntax."""
    if not reference.startswith("git:"):
        return load(Path(reference))

    ref = reference[len("git:") :]
    relative = DEFAULT_ONTOLOGY_PATH.relative_to(REPO_ROOT)
    try:
        content = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{ref}:{relative}"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or exc
        raise OntologyError(f"cannot read ontology at git ref {ref!r}: {detail}") from exc

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        return load(temporary)
    finally:
        temporary.unlink(missing_ok=True)


def _show(args: argparse.Namespace) -> int:
    ontology = load(args.ontology)

    if args.type:
        return _show_type(ontology, args.type)

    print(f"{ontology.name} v{ontology.version}\n")
    print("Entity types:")
    for name, resolved in sorted(ontology.entity_types.items()):
        marker = "  (abstract)" if resolved.abstract else ""
        parents = f" < {' < '.join(resolved.ancestors)}" if resolved.ancestors else ""
        print(f"  {name}{parents}{marker}")

    print("\nRelationship types:")
    for name, rel in sorted(ontology.relationship_types.items()):
        arrow = "->" if rel.directed else "--"
        print(f"  ({'|'.join(rel.source)}) {arrow}[{name}]{arrow} ({'|'.join(rel.target)})")

    print("\nSystem relationship types:")
    for name in sorted(ontology.system_relationship_types):
        print(f"  {name}")
    return 0


def _show_type(ontology: Ontology, name: str) -> int:
    resolved = ontology.entity_type(name)
    print(f"{name} — {resolved.spec.label}")
    if resolved.spec.description:
        print(f"\n{' '.join(resolved.spec.description.split())}")
    print(f"\nLabels in Neo4j: {':'.join(resolved.labels)}")
    if not resolved.abstract:
        print(f"Display template: {resolved.spec.display_name}")

    print("\nProperties:")
    for prop_name, prop in sorted(resolved.properties.items()):
        flags = [prop.type.value]
        if prop.required:
            flags.append("required")
        if prop.multi:
            flags.append("multi")
        if prop.indexed:
            flags.append("indexed")
        if prop.pii:
            flags.append("pii")
        print(f"  {prop_name:<24} {', '.join(flags)}")

    if resolved.spec.identifiers:
        print(f"\nStrong identifiers: {', '.join(resolved.spec.identifiers)}")
    print(f"\nMay be the source of: {', '.join(ontology.relationships_from(name))}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
