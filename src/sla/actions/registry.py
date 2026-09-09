"""The registry of available actions.

Actions register themselves here, and the right-click menu is built from what
is registered and applicable to the node under the cursor. Adding a capability
is a new file plus one ``register()`` call, not a change to the interface.
"""

from __future__ import annotations

from sla.actions.base import Action, ActionDescriptor, missing_requirements
from sla.config import Settings
from sla.ontology import Ontology

_actions: dict[str, Action] = {}


def register(action: Action) -> Action:
    """Add an action to the registry. Registering the same id twice is an error."""
    if action.id in _actions:
        raise ValueError(f"an action with id {action.id!r} is already registered")
    _actions[action.id] = action
    return action


def get(action_id: str) -> Action:
    try:
        return _actions[action_id]
    except KeyError:
        raise KeyError(f"no action {action_id!r}") from None


def all_actions() -> dict[str, Action]:
    return dict(_actions)


def clear() -> None:
    """Empty the registry. For tests."""
    _actions.clear()


def applicable_to(
    entity_type: str, ontology: Ontology, settings: Settings
) -> list[ActionDescriptor]:
    """Actions that may be invoked on an entity of this type.

    Input types are matched through the ontology's inheritance, so an action
    declaring ``LegalEntity`` is offered on a Company. Actions whose
    credentials are missing are still listed, but marked unavailable and with
    the reason, which is friendlier than hiding them and leaving the user to
    wonder where the registry lookup went.
    """
    resolved = ontology.entity_type(entity_type)
    descriptors: list[ActionDescriptor] = []

    for action in _actions.values():
        accepts = any(
            declared == entity_type or declared in resolved.ancestors
            for declared in action.input_types
        )
        if not accepts:
            continue

        missing = missing_requirements(action, settings)
        descriptors.append(
            ActionDescriptor(
                id=action.id,
                label=action.label,
                description=action.description,
                input_types=action.input_types,
                output_types=action.output_types,
                default_policy=action.default_policy,
                available=not missing,
                unavailable_reason=(
                    f"needs {', '.join(missing)} to be configured" if missing else ""
                ),
            )
        )

    return sorted(descriptors, key=lambda d: d.label)
