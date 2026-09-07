"""Shared helpers for the generators."""

from __future__ import annotations

from sla.ontology.spec import PropertyType

BANNER = (
    "Generated from ontology/ontology.yaml by `sla-ontology generate`.\n"
    "Do not edit by hand: edit the ontology and regenerate."
)

# How each ontology property type is expressed in Python.
PYTHON_TYPES: dict[PropertyType, str] = {
    PropertyType.STRING: "str",
    PropertyType.TEXT: "str",
    PropertyType.INTEGER: "int",
    PropertyType.NUMBER: "float",
    PropertyType.BOOLEAN: "bool",
    PropertyType.DATE: "date",
    PropertyType.DATETIME: "datetime",
    PropertyType.URL: "str",
    PropertyType.EMAIL: "str",
    PropertyType.PHONE: "str",
    PropertyType.COUNTRY: "str",
    PropertyType.CURRENCY: "str",
    PropertyType.ENUM: "str",
}

# ...and in JSON Schema, as (type, format-or-None).
JSON_TYPES: dict[PropertyType, tuple[str, str | None]] = {
    PropertyType.STRING: ("string", None),
    PropertyType.TEXT: ("string", None),
    PropertyType.INTEGER: ("integer", None),
    PropertyType.NUMBER: ("number", None),
    PropertyType.BOOLEAN: ("boolean", None),
    PropertyType.DATE: ("string", "date"),
    PropertyType.DATETIME: ("string", "date-time"),
    PropertyType.URL: ("string", "uri"),
    PropertyType.EMAIL: ("string", "email"),
    PropertyType.PHONE: ("string", None),
    PropertyType.COUNTRY: ("string", None),
    PropertyType.CURRENCY: ("string", None),
    PropertyType.ENUM: ("string", None),
}


def docstring(text: str, indent: str = "    ") -> str:
    """Render ``text`` as a Python docstring, wrapped at a readable width."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    if len(collapsed) + len(indent) + 6 <= 88:
        return f'{indent}"""{collapsed}"""\n'

    words = collapsed.split()
    lines: list[str] = []
    current = indent
    for word in words:
        candidate = f"{current} {word}" if current != indent else f"{current}{word}"
        if len(candidate) > 84 and current != indent:
            lines.append(current)
            current = f"{indent}{word}"
        else:
            current = candidate
    lines.append(current)
    body = "\n".join(lines)
    return f'{indent}"""\n{body}\n{indent}"""\n'
