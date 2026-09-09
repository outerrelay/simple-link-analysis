"""What an action is.

An action is something you invoke on a node: expand it from the database, look
it up in a company registry, later search the news for it. Every one has the
same shape — declared input and output entity types, runs as a background job,
and returns a proposal rather than writing to the graph.

Maltego calls these "transforms". An action is simply what the right-click menu
offers, so that is what they are called here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sla.app.staging import Proposal
from sla.config import Settings, WritePolicy
from sla.graph.model import EntityRecord
from sla.graph.repository import GraphRepository
from sla.ontology import Ontology


class ActionError(Exception):
    """The action could not run — bad input, missing credentials, a dead API."""


@dataclass
class ActionContext:
    """Everything an action is given to do its work."""

    entity: EntityRecord
    """The node the action was invoked on."""

    repository: GraphRepository
    ontology: Ontology
    settings: Settings
    options: dict[str, object]
    """Per-invocation options from the menu, e.g. expansion depth."""


@runtime_checkable
class Action(Protocol):
    """The interface every action implements."""

    id: str
    label: str
    description: str
    input_types: tuple[str, ...]
    """Entity types this may be invoked on. Abstract types are expanded."""

    output_types: tuple[str, ...]
    default_policy: WritePolicy
    """What happens to the result unless overridden per invocation."""

    requires: tuple[str, ...]
    """Settings that must be non-empty, e.g. ``companies_house_api_key``."""

    async def run(self, context: ActionContext) -> Proposal: ...


@dataclass
class ActionDescriptor:
    """An action as described to the interface."""

    id: str
    label: str
    description: str
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    default_policy: WritePolicy
    available: bool
    """False when a required credential is missing."""

    unavailable_reason: str = ""


def missing_requirements(action: Action, settings: Settings) -> list[str]:
    """Which of the action's required settings are unset."""
    return [name for name in action.requires if not getattr(settings, name, "")]
