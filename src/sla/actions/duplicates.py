"""Ask whether a node is already in the database.

The counterpart to the background sweep: that one looks across the whole graph
periodically, this one answers the question for the node under the cursor —
which is what you want the moment a registry lookup returns a company you
suspect you already have.

It only ever reports. Merging is a separate, explicit instruction.
"""

from __future__ import annotations

from sla.actions.base import ActionContext
from sla.actions.registry import register
from sla.app.staging import Proposal
from sla.config import WritePolicy
from sla.graph.identity import IdentityResolver


class CheckForDuplicates:
    """Find nodes that may be the same thing as this one."""

    id = "identity.check"
    label = "Check for duplicates"
    description = (
        "Look for other records that may be the same thing, by shared "
        "identifier, matching registration number or similar name."
    )
    input_types = ("Thing",)
    output_types = ()
    default_policy = WritePolicy.AUTO_COMMIT
    """Nothing is written, so there is nothing to review."""

    requires: tuple[str, ...] = ()
    external = False

    async def run(self, context: ActionContext) -> Proposal:
        resolver = IdentityResolver(
            context.repository._driver,  # noqa: SLF001 - same layer, one driver
            context.ontology,
            context.repository._database,  # noqa: SLF001
        )
        matches = await resolver.matches_for(context.entity.id)

        if not matches:
            summary = "no possible duplicates found"
        else:
            strong = sum(1 for m in matches if m.strength == "strong")
            summary = (
                f"{len(matches)} possible duplicate"
                f"{'' if len(matches) == 1 else 's'}"
                f"{f', {strong} strong' if strong else ''}"
            )

        # Matches are reported through the job, not proposed as graph writes:
        # the answer is "look at these", not "add these".
        proposal = Proposal(action_id=self.id, summary=summary)
        proposal.matches = [  # type: ignore[attr-defined]
            {
                "entity_id": m.entity_id,
                "name": m.name,
                "type": m.type,
                "reason": m.reason,
                "strength": m.strength,
            }
            for m in matches
        ]
        return proposal


register(CheckForDuplicates())
