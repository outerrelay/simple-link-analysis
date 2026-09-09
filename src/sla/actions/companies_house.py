"""Look up a UK company and its officers at Companies House.

Companies House publishes the UK register through a free API that needs a key,
sent as HTTP Basic auth with the key as the username and no password.

Two actions rather than one, because they answer different questions and an
analyst usually wants one or the other: the company's own details, or who runs
it. The officers lookup is where the interesting links are.

The response shapes below follow the published API. They have not been
exercised against the live service from this project, so parsing is defensive:
missing fields are skipped rather than assumed.
"""

from __future__ import annotations

from typing import Any

import httpx

from sla.actions.base import ActionContext, ActionError
from sla.actions.registry import register
from sla.app.staging import Proposal, ProposedEntity, ProposedRelationship
from sla.config import Settings, WritePolicy
from sla.graph.model import new_id

API_ROOT = "https://api.company-information.service.gov.uk"
TIMEOUT = 20.0

# Companies House officer roles, mapped onto ontology relationships. Roles not
# listed here still produce a DIRECTOR_OF edge with the raw role recorded, so
# an unfamiliar role is visible rather than silently dropped.
OFFICER_RELATIONSHIPS = {
    "director": "DIRECTOR_OF",
    "corporate-director": "DIRECTOR_OF",
    "nominee-director": "DIRECTOR_OF",
    "secretary": "OFFICER_OF",
    "corporate-secretary": "OFFICER_OF",
    "llp-member": "MEMBER_OF",
    "llp-designated-member": "MEMBER_OF",
}


async def _get(path: str, settings: Settings) -> dict[str, Any]:
    """Call the API with the key as the Basic auth username."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{API_ROOT}{path}", auth=(settings.companies_house_api_key, "")
            )
            if response.status_code == 401:
                raise ActionError("Companies House rejected the API key")
            if response.status_code == 404:
                raise ActionError("Companies House has no record for that number")
            if response.status_code == 429:
                raise ActionError("Companies House rate limit reached; try again shortly")
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise ActionError(f"Companies House request failed: {exc}") from exc


def _company_number(context: ActionContext) -> str:
    number = context.entity.properties.get("registration_number")
    if not number:
        raise ActionError("this company has no registration number; add one before looking it up")
    return str(number).strip()


class CompaniesHouseProfile:
    """Fetch the company's own registered details."""

    id = "companies_house.profile"
    label = "Companies House: company details"
    description = "Fetch registered name, status, incorporation date and address."
    input_types = ("Company",)
    output_types = ("Company", "Address")
    default_policy = WritePolicy.REVIEW
    requires = ("companies_house_api_key",)

    async def run(self, context: ActionContext) -> Proposal:
        number = _company_number(context)
        payload = await _get(f"/company/{number}", context.settings)
        return build_profile_proposal(self.id, context.entity.id, payload)


class CompaniesHouseOfficers:
    """Fetch the people who run the company."""

    id = "companies_house.officers"
    label = "Companies House: officers"
    description = "Fetch directors and secretaries, with their appointment dates."
    input_types = ("Company",)
    output_types = ("Person", "Address")
    default_policy = WritePolicy.REVIEW
    requires = ("companies_house_api_key",)

    async def run(self, context: ActionContext) -> Proposal:
        number = _company_number(context)
        payload = await _get(f"/company/{number}/officers?items_per_page=100", context.settings)
        return build_officers_proposal(self.id, context.entity.id, payload)


# --- parsing, kept separate so it can be tested without the network ---------


def build_profile_proposal(action_id: str, subject_id: str, payload: dict[str, Any]) -> Proposal:
    """Turn a company profile response into proposed updates."""
    entities: list[ProposedEntity] = []
    relationships: list[ProposedRelationship] = []

    properties = {
        "name": payload.get("company_name"),
        "registration_number": payload.get("company_number"),
        "status": _status(payload.get("company_status")),
        "incorporation_date": payload.get("date_of_creation"),
        "dissolution_date": payload.get("date_of_cessation"),
        "legal_form": payload.get("type"),
        "jurisdiction": "GB",
        "sector": [str(code) for code in payload.get("sic_codes") or []],
    }
    # Update the entity in place rather than creating a second one beside it.
    entities.append(
        ProposedEntity(
            type="Company",
            id=new_id(),
            existing_id=subject_id,
            properties={k: v for k, v in properties.items() if v not in (None, "", [])},
        )
    )

    address = _address(payload.get("registered_office_address"))
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

    return Proposal(
        action_id=action_id,
        entities=entities,
        relationships=relationships,
        summary=payload.get("company_name") or "company profile",
    )


def build_officers_proposal(action_id: str, subject_id: str, payload: dict[str, Any]) -> Proposal:
    """Turn an officers response into proposed people and appointments."""
    entities: list[ProposedEntity] = []
    relationships: list[ProposedRelationship] = []

    for item in payload.get("items") or []:
        name = item.get("name")
        if not name:
            continue

        role = str(item.get("officer_role") or "").lower()
        person = ProposedEntity(
            type="Person",
            id=new_id(),
            properties={
                key: value
                for key, value in {
                    "name": _readable_name(name),
                    "last_name": _surname(name),
                    "nationality": _nationality(item.get("nationality")),
                    "birth_date": _birth_date(item.get("date_of_birth")),
                }.items()
                if value not in (None, "", [])
            },
        )
        entities.append(person)

        relationships.append(
            ProposedRelationship(
                type=OFFICER_RELATIONSHIPS.get(role, "DIRECTOR_OF"),
                source_id=person.id,
                target_id=subject_id,
                properties={"role": role} if role else {},
                valid_from=item.get("appointed_on"),
                valid_to=item.get("resigned_on"),
            )
        )

    count = len(entities)
    return Proposal(
        action_id=action_id,
        entities=entities,
        relationships=relationships,
        summary=f"{count} officer{'' if count == 1 else 's'}",
    )


def _readable_name(name: str) -> str:
    """Companies House writes names as ``SURNAME, Firstname``."""
    if "," not in name:
        return name.strip()
    surname, given = (part.strip() for part in name.split(",", 1))
    return f"{given} {surname.title()}".strip()


def _surname(name: str) -> str | None:
    return name.split(",", 1)[0].strip().title() if "," in name else None


def _nationality(value: Any) -> list[str]:
    """Nationality arrives as free text like "British", not an ISO code.

    Rather than guess a country code and record something false, the value is
    kept only when it is already a two-letter code.
    """
    text = str(value or "").strip()
    return [text.upper()] if len(text) == 2 and text.isalpha() else []


def _birth_date(block: Any) -> str | None:
    """Only month and year are published, so the day is unknowable.

    Recording the first of the month would assert a precision the source does
    not have, so a partial date is left out entirely.
    """
    if not isinstance(block, dict):
        return None
    if block.get("day") and block.get("month") and block.get("year"):
        return f"{block['year']:04d}-{block['month']:02d}-{block['day']:02d}"
    return None


def _status(value: Any) -> str | None:
    """Map the API's status vocabulary onto the ontology's enum."""
    mapping = {
        "active": "active",
        "dissolved": "dissolved",
        "liquidation": "liquidation",
        "administration": "liquidation",
        "open": "active",
        "closed": "dissolved",
    }
    return mapping.get(str(value or "").lower())


def _address(block: Any) -> ProposedEntity | None:
    if not isinstance(block, dict):
        return None
    lines = [
        block.get("address_line_1"),
        block.get("address_line_2"),
    ]
    street = ", ".join(str(line) for line in lines if line)
    parts = [street, block.get("postal_code"), block.get("locality"), block.get("country")]
    display = ", ".join(str(part) for part in parts if part)
    if not display:
        return None

    return ProposedEntity(
        type="Address",
        id=new_id(),
        properties={
            key: value
            for key, value in {
                "name": display,
                "street": street or None,
                "city": block.get("locality"),
                "postal_code": block.get("postal_code"),
                "region": block.get("region"),
            }.items()
            if value
        },
    )


register(CompaniesHouseProfile())
register(CompaniesHouseOfficers())
