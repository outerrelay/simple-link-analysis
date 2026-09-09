"""The meta-schema: what a valid ontology file may contain.

These models describe the *shape of the ontology file itself*, not the data it
governs. Parsing ``ontology.yaml`` into them catches malformed declarations
before anything downstream tries to generate code from them.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Property names become Python attributes and Neo4j property keys.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
ENTITY_TYPE_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")
RELATIONSHIP_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class PropertyType(str, Enum):
    """The value types a property may take.

    Kept deliberately small: each one must map onto a Python type, a JSON
    Schema fragment and a Neo4j-storable value.
    """

    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    URL = "url"
    EMAIL = "email"
    PHONE = "phone"
    COUNTRY = "country"
    CURRENCY = "currency"
    ENUM = "enum"


class PropertySpec(BaseModel):
    """Declaration of a single property on an entity or relationship type."""

    model_config = ConfigDict(extra="forbid")

    type: PropertyType
    description: str = ""
    required: bool = False
    multi: bool = Field(default=False, description="Property holds a list of values.")
    enum: list[str] | None = None
    indexed: bool = Field(default=False, description="Give this property a Neo4j index.")
    pii: bool = Field(
        default=False,
        description="Marks personal data, so it can be reported on or redacted.",
    )

    @model_validator(mode="after")
    def check_enum_values(self) -> PropertySpec:
        if self.type is PropertyType.ENUM and not self.enum:
            raise ValueError("a property of type 'enum' must list its allowed values")
        if self.type is not PropertyType.ENUM and self.enum:
            raise ValueError("'enum' values are only meaningful for type 'enum'")
        return self


class EntityTypeSpec(BaseModel):
    """Declaration of an entity type."""

    model_config = ConfigDict(extra="forbid")

    label: str
    plural: str = ""
    description: str = ""
    abstract: bool = Field(
        default=False,
        description="Abstract types are never instantiated; they group subtypes.",
    )
    extends: str | None = None
    ftm: str | None = Field(default=None, description="Corresponding FollowTheMoney schema.")
    icon: str | None = None
    color: str | None = None
    display_name: str = Field(
        default="{name}",
        description="Template for the canvas label; {placeholders} are property names.",
    )
    properties: dict[str, PropertySpec] = Field(default_factory=dict)
    identifiers: list[str] = Field(
        default_factory=list,
        description="Properties that identify the entity strongly enough to "
        "propose a SAME_AS candidate when two entities share one.",
    )
    canonical_key: list[str] = Field(
        default_factory=list,
        description="Properties that make this type canonical: one node per "
        "distinct combination, enforced by a uniqueness constraint. Use for "
        "types whose whole purpose is to be shared, such as a phone number "
        "two people both use.",
    )

    @model_validator(mode="after")
    def check_property_names(self) -> EntityTypeSpec:
        for name in self.properties:
            if not NAME_PATTERN.match(name):
                raise ValueError(f"property name {name!r} must be lower_snake_case")
        return self


class RelationshipTypeSpec(BaseModel):
    """Declaration of a relationship type."""

    model_config = ConfigDict(extra="forbid")

    label: str
    description: str = ""
    ftm: str | None = None
    source: list[str] = Field(min_length=1, description="Permitted source entity types.")
    target: list[str] = Field(min_length=1, description="Permitted target entity types.")
    directed: bool = True
    symmetric: bool = Field(
        default=False,
        description="Holds equally in both directions; implies undirected.",
    )
    temporal: bool = Field(
        default=True,
        description="Carries valid_from/valid_to. Everything records observed_at.",
    )
    properties: dict[str, PropertySpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_symmetry_and_names(self) -> RelationshipTypeSpec:
        if self.symmetric and self.directed:
            raise ValueError("a symmetric relationship cannot also be directed")
        for name in self.properties:
            if not NAME_PATTERN.match(name):
                raise ValueError(f"property name {name!r} must be lower_snake_case")
        return self


class OntologySpec(BaseModel):
    """The ontology file as parsed, before cross-references are resolved."""

    model_config = ConfigDict(extra="forbid")

    version: str
    name: str
    description: str = ""
    entity_types: dict[str, EntityTypeSpec]
    relationship_types: dict[str, RelationshipTypeSpec] = Field(default_factory=dict)
    system_relationship_types: dict[str, RelationshipTypeSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_type_names(self) -> OntologySpec:
        for name in self.entity_types:
            if not ENTITY_TYPE_PATTERN.match(name):
                raise ValueError(f"entity type {name!r} must be PascalCase")
        overlap = set(self.relationship_types) & set(self.system_relationship_types)
        if overlap:
            raise ValueError(f"relationship types declared twice: {sorted(overlap)}")
        for name in {**self.relationship_types, **self.system_relationship_types}:
            if not RELATIONSHIP_TYPE_PATTERN.match(name):
                raise ValueError(f"relationship type {name!r} must be UPPER_SNAKE_CASE")
        return self
