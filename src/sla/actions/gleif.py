"""Look up an entity's Legal Entity Identifier record at GLEIF.

GLEIF publishes the global LEI register openly, with no API key. An LEI is a
strong identifier, so what this mainly buys is identity: attaching the
canonical Identifier node lets duplicate detection notice that two records are
the same company.

The response shape below follows GLEIF's documented JSON:API v1 format. It has
not been exercised against the live service from this project, so the parser is
written to tolerate missing fields rather than assume them.
"""

from __future__ import annotations

from typing import Any

import httpx

from sla.actions.base import ActionContext, ActionError
from sla.actions.registry import register
from sla.app.staging import Proposal, ProposedEntity, ProposedRelationship
from sla.config import WritePolicy
from sla.graph.model import new_id

GLEIF_API = "https://api.gleif.org/api/v1/lei-records"
TIMEOUT = 20.0


class GleifLookup:
    """Find the LEI record for a company and attach what it says."""

    id = "gleif.lookup"
    label = "Look up LEI (GLEIF)"
    description = (
        "Search the open Legal Entity Identifier register for this company and "
        "attach its LEI, registered address and legal form."
    )
    input_types = ("LegalEntity",)
    output_types = ("Identifier", "Address", "Company")
    default_policy = WritePolicy.REVIEW
    requires: tuple[str, ...] = ()
    external = True

    async def run(self, context: ActionContext) -> Proposal:
        entity = context.entity
        name = entity.properties.get("name")
        if not name:
            raise ActionError("this entity has no name to search for")

        params: dict[str, str] = {"page[size]": "5"}
        registration_number = entity.properties.get("registration_number")
        if registration_number:
            # Searching by registration number is far more precise than by
            # name, so prefer it when the entity carries one.
            params["filter[entity.registeredAs]"] = str(registration_number)
        else:
            params["filter[entity.legalName]"] = str(name)

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(GLEIF_API, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ActionError(f"GLEIF request failed: {exc}") from exc

        return build_proposal(self.id, entity.id, payload)


def build_proposal(action_id: str, subject_id: str, payload: dict[str, Any]) -> Proposal:
    """Turn a GLEIF response into proposed nodes and edges.

    Separate from the HTTP call so it can be tested against recorded responses.
    """
    records = payload.get("data") or []
    entities: list[ProposedEntity] = []
    relationships: list[ProposedRelationship] = []

    for record in records:
        attributes = record.get("attributes") or {}
        lei = attributes.get("lei") or record.get("id")
        entity_block = attributes.get("entity") or {}
        if not lei:
            continue

        identifier = ProposedEntity(
            type="Identifier",
            id=new_id(),
            properties={
                "name": f"lei:{lei}",
                "scheme": "lei",
                "value": str(lei),
                "authority": "GLEIF",
            },
        )
        entities.append(identifier)
        relationships.append(
            ProposedRelationship(
                type="HAS_IDENTIFIER", source_id=subject_id, target_id=identifier.id
            )
        )

        address = _address(entity_block.get("legalAddress"))
        if address is not None:
            entities.append(address)
            relationships.append(
                ProposedRelationship(
                    type="REGISTERED_AT",
                    source_id=subject_id,
                    target_id=address.id,
                    properties={"address_type": "registered"},
                )
            )

    summary = (
        f"{len(records)} LEI record{'' if len(records) == 1 else 's'}"
        if records
        else "no LEI record found"
    )
    return Proposal(
        action_id=action_id,
        entities=entities,
        relationships=relationships,
        summary=summary,
    )


def _address(block: dict[str, Any] | None) -> ProposedEntity | None:
    if not block:
        return None
    lines = [line for line in (block.get("addressLines") or []) if line]
    parts = [*lines, block.get("postalCode"), block.get("city"), block.get("country")]
    display = ", ".join(str(part) for part in parts if part)
    if not display:
        return None

    return ProposedEntity(
        type="Address",
        id=new_id(),
        properties={
            "name": display,
            "street": ", ".join(str(line) for line in lines) or None,
            "city": block.get("city"),
            "postal_code": block.get("postalCode"),
            "region": block.get("region"),
            "country": block.get("country"),
        },
    )


register(GleifLookup())
