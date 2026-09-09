"""Generated from ontology/ontology.yaml by `sla-ontology generate`.
Do not edit by hand: edit the ontology and regenerate."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EntityBase(BaseModel):
    """Fields every stored entity carries, supplied by the system."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="UUID assigned on creation; never a natural key.")
    observed_at: datetime | None = Field(
        default=None, description="When this entity was last confirmed by a source."
    )


class RelationshipBase(BaseModel):
    """Fields every stored relationship carries, supplied by the system."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="UUID assigned on creation.")
    source_id: str = Field(description="UUID of the entity the edge starts at.")
    target_id: str = Field(description="UUID of the entity the edge ends at.")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence in the claim."
    )
    observed_at: datetime | None = Field(
        default=None, description="When this relationship was last confirmed."
    )
    assertion_ids: list[str] = Field(
        default_factory=list,
        description="Assertions supporting this edge; it exists while one does.",
    )


class TemporalRelationshipBase(RelationshipBase):
    """A relationship that can start and stop being true."""

    valid_from: date | None = Field(
        default=None, description="When this began to hold in the world."
    )
    valid_to: date | None = Field(
        default=None, description="When it ceased to hold, if it has."
    )


# ----------------------------------------------------------------------
# Entities
# ----------------------------------------------------------------------


class Address(EntityBase):
    """A postal address."""
    type: Literal["Address"] = "Address"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    street: str | None = Field(default=None, description='Street and number.')
    city: str | None = Field(default=None, description='City or town.')
    postal_code: str | None = Field(default=None, description='Postal or ZIP code.')
    region: str | None = Field(default=None, description='State, province or county.')
    country: str | None = Field(default=None, description='Country.')
    latitude: float | None = Field(default=None, description='Decimal degrees north.')
    longitude: float | None = Field(default=None, description='Decimal degrees east.')


class ApiRecord(EntityBase):
    """
    A response from a registry or data provider, retained so that any claim derived
    from it can be re-checked against what was returned.
    """
    type: Literal["ApiRecord"] = "ApiRecord"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    retrieved_at: datetime = Field(description='When this source was captured.')
    content_hash: str | None = Field(default=None, description='SHA-256 of the stored content, for deduplication.')
    provider: str = Field(description='Data provider, e.g. companies_house, gleif.')
    endpoint: str | None = Field(default=None, description='Endpoint or query that produced the record.')
    query: str | None = Field(default=None, description='Parameters used.')


class BankAccount(EntityBase):
    """An account held at a financial institution."""
    type: Literal["BankAccount"] = "BankAccount"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    value_amount: float | None = Field(default=None, description='Most recent known value.')
    value_currency: str | None = Field(default=None, description='Currency of the value.')
    valuation_date: date | None = Field(default=None, description='Date the value was assessed.')
    iban: str | None = Field(default=None, description='International bank account number.')
    bic: str | None = Field(default=None, description='SWIFT/BIC code of the institution.')
    bank_name: str | None = Field(default=None, description='Name of the institution.')
    currency: str | None = Field(default=None, description='Denominating currency.')


class Company(EntityBase):
    """An incorporated commercial entity."""
    type: Literal["Company"] = "Company"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    jurisdiction: str | None = Field(default=None, description='Country whose law the entity is constituted under.')
    registration_number: str | None = Field(default=None, description='Identifier issued by the registering authority.')
    incorporation_date: date | None = Field(default=None, description='Date the entity came into existence.')
    dissolution_date: date | None = Field(default=None, description='Date the entity ceased to exist, if it has.')
    status: Literal["active", "dissolved", "liquidation", "dormant", "unknown"] | None = Field(default=None, description='Lifecycle status as reported by the registry.')
    legal_form: str | None = Field(default=None, description='Legal form as stated by the registry, e.g. GmbH, Ltd, AS.')
    sector: list[str] = Field(default_factory=list, description='Industry classification.')


class Document(EntityBase):
    """An uploaded file — PDF, Word, spreadsheet or similar."""
    type: Literal["Document"] = "Document"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    retrieved_at: datetime = Field(description='When this source was captured.')
    content_hash: str | None = Field(default=None, description='SHA-256 of the stored content, for deduplication.')
    filename: str | None = Field(default=None, description='Original filename as uploaded.')
    media_type: str | None = Field(default=None, description='IANA media type.')
    page_count: int | None = Field(default=None, description='Number of pages, where applicable.')
    published_date: date | None = Field(default=None, description='Date the document itself is dated.')
    author: str | None = Field(default=None, description='Stated author.')


class EmailAddress(EntityBase):
    """
    An email address, canonical on its lowercased form so that a shared mailbox
    links the people who use it.
    """
    type: Literal["EmailAddress"] = "EmailAddress"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    address: str = Field(description='The address, lowercased. Identity.')
    domain: str | None = Field(default=None, description='Domain part, indexed so shared domains are findable.')


class Identifier(EntityBase):
    """
    A strong identifier issued by an authority. Modelled as a node so that two
    entities bearing the same one can be detected as duplicate candidates by a
    single query.
    """
    type: Literal["Identifier"] = "Identifier"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    scheme: str = Field(description='Issuing scheme, e.g. lei, orgnr, companies_house, passport.')
    value: str = Field(description='The identifier itself, normalised.')
    authority: str | None = Field(default=None, description='Body that issued it.')


class LegalCase(EntityBase):
    """Court or regulatory proceedings."""
    type: Literal["LegalCase"] = "LegalCase"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    case_number: str | None = Field(default=None, description='Docket or case reference.')
    court: str | None = Field(default=None, description='Court or tribunal.')
    jurisdiction: str | None = Field(default=None, description='Jurisdiction hearing the case.')
    filed_date: date | None = Field(default=None, description='Date proceedings began.')
    case_type: Literal["criminal", "civil", "administrative", "insolvency", "regulatory", "unknown"] | None = Field(default=None, description='Nature of the proceedings.')
    status: Literal["open", "closed", "appealed", "settled", "unknown"] | None = Field(default=None, description='Current status.')
    outcome: str | None = Field(default=None, description='Outcome, where concluded.')


class NewsArticle(EntityBase):
    """A journalistic article, typically found by online search."""
    type: Literal["NewsArticle"] = "NewsArticle"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    retrieved_at: datetime = Field(description='When this source was captured.')
    content_hash: str | None = Field(default=None, description='SHA-256 of the stored content, for deduplication.')
    url: str = Field(description='Address of the article.')
    publication: str | None = Field(default=None, description='Outlet that published it.')
    published_date: date | None = Field(default=None, description='Publication date.')
    author: str | None = Field(default=None, description='Byline.')
    summary: str | None = Field(default=None, description="Summary of the article's relevance.")
    language: str | None = Field(default=None, description='ISO 639-1 language code.')


class Organisation(EntityBase):
    """
    A body that is not a commercial company: public authority, NGO, political party,
    association or similar.
    """
    type: Literal["Organisation"] = "Organisation"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    jurisdiction: str | None = Field(default=None, description='Country whose law the entity is constituted under.')
    registration_number: str | None = Field(default=None, description='Identifier issued by the registering authority.')
    incorporation_date: date | None = Field(default=None, description='Date the entity came into existence.')
    dissolution_date: date | None = Field(default=None, description='Date the entity ceased to exist, if it has.')
    status: Literal["active", "dissolved", "liquidation", "dormant", "unknown"] | None = Field(default=None, description='Lifecycle status as reported by the registry.')
    category: Literal["public_body", "ngo", "political_party", "association", "religious", "other"] | None = Field(default=None, description='Kind of organisation.')


class Person(EntityBase):
    """A natural person."""
    type: Literal["Person"] = "Person"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    first_name: str | None = Field(default=None, description='Given name.')
    last_name: str | None = Field(default=None, description='Family name.')
    birth_date: date | None = Field(default=None, description='Date of birth.')
    death_date: date | None = Field(default=None, description='Date of death, if known.')
    nationality: list[str] = Field(default_factory=list, description='Citizenships held.')
    gender: Literal["female", "male", "other", "unknown"] | None = Field(default=None, description='Gender as recorded by the source.')
    title: str | None = Field(default=None, description='Honorific or academic title.')
    position: list[str] = Field(default_factory=list, description='Public role, used chiefly to flag politically exposed persons.')


class PhoneNumber(EntityBase):
    """
    A telephone number. Canonical on its E.164 form, so that two people who both use
    a number are attached to the same node — which is the only reason to model a
    phone number as a node at all.
    """
    type: Literal["PhoneNumber"] = "PhoneNumber"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    e164: str = Field(description='The number in E.164 form, e.g. +4712345678. Identity.')
    country: str | None = Field(default=None, description='Country the number is registered in.')
    line_type: Literal["mobile", "landline", "voip", "fax", "unknown"] | None = Field(default=None, description='Kind of line.')


class PublicTender(EntityBase):
    """A public procurement notice or contract award."""
    type: Literal["PublicTender"] = "PublicTender"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    reference: str | None = Field(default=None, description='Notice reference issued by the procurement portal.')
    published_date: date | None = Field(default=None, description='Date of publication.')
    estimated_amount: float | None = Field(default=None, description='Estimated contract value.')
    currency: str | None = Field(default=None, description='Currency of the estimate.')
    cpv_codes: list[str] = Field(default_factory=list, description='Common Procurement Vocabulary codes.')
    procedure_type: str | None = Field(default=None, description='Procurement procedure used.')


class RealEstate(EntityBase):
    """Land or buildings."""
    type: Literal["RealEstate"] = "RealEstate"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    value_amount: float | None = Field(default=None, description='Most recent known value.')
    value_currency: str | None = Field(default=None, description='Currency of the value.')
    valuation_date: date | None = Field(default=None, description='Date the value was assessed.')
    cadastral_reference: str | None = Field(default=None, description='Land registry reference.')
    area_sqm: float | None = Field(default=None, description='Area in square metres.')
    property_type: str | None = Field(default=None, description='Residential, commercial, agricultural and so on.')


class SanctionsListing(EntityBase):
    """An entry on a sanctions or watch list."""
    type: Literal["SanctionsListing"] = "SanctionsListing"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    authority: str = Field(description='Issuing authority, e.g. OFAC, EU, UK OFSI.')
    programme: str | None = Field(default=None, description='Sanctions programme.')
    listed_date: date | None = Field(default=None, description='Date the listing took effect.')
    delisted_date: date | None = Field(default=None, description='Date the listing was lifted, if it has been.')
    reason: str | None = Field(default=None, description='Stated grounds for listing.')


class Transaction(EntityBase):
    """
    A transfer of value. Modelled as a node rather than an edge so that payer, payee
    and supporting documents can all attach to it.
    """
    type: Literal["Transaction"] = "Transaction"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    amount: float = Field(description='Amount transferred.')
    currency: str = Field(description='Currency of the amount.')
    transaction_date: date | None = Field(default=None, description='Date of the transfer.')
    purpose: str | None = Field(default=None, description='Stated purpose or reference.')


class Vessel(EntityBase):
    """A ship. Relevant to sanctions and trade investigations."""
    type: Literal["Vessel"] = "Vessel"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    value_amount: float | None = Field(default=None, description='Most recent known value.')
    value_currency: str | None = Field(default=None, description='Currency of the value.')
    valuation_date: date | None = Field(default=None, description='Date the value was assessed.')
    imo_number: str | None = Field(default=None, description='IMO ship identification number.')
    flag: str | None = Field(default=None, description='Country of registration.')
    tonnage: float | None = Field(default=None, description='Gross tonnage.')
    call_sign: str | None = Field(default=None, description='Radio call sign.')


class WebPage(EntityBase):
    """A page captured from the web."""
    type: Literal["WebPage"] = "WebPage"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    retrieved_at: datetime = Field(description='When this source was captured.')
    content_hash: str | None = Field(default=None, description='SHA-256 of the stored content, for deduplication.')
    url: str = Field(description='Address the page was retrieved from.')


class Website(EntityBase):
    """A website or web domain."""
    type: Literal["Website"] = "Website"
    name: str = Field(description='Primary display name for the entity.')
    aliases: list[str] = Field(default_factory=list, description='Alternative names, transliterations and former names.')
    notes: str | None = Field(default=None, description='Free-text analyst notes.')
    url: str | None = Field(default=None, description='Canonical URL.')
    domain: str = Field(description='Registered domain, lowercased and without a leading www. Identity: the ontology treats a Website as a site, not a page.')


# ----------------------------------------------------------------------
# Relationships
# ----------------------------------------------------------------------


class AssociateOf(TemporalRelationshipBase):
    """
    Known connection that is not familial, employment or ownership. Deliberately
    loose; use a more specific type where one fits.
    """
    type: Literal["ASSOCIATE_OF"] = "ASSOCIATE_OF"
    basis: str | None = Field(default=None, description='What the association rests on.')


class Awarded(RelationshipBase):
    """Won the contract or one of its lots."""
    type: Literal["AWARDED"] = "AWARDED"
    lot: str | None = Field(default=None, description='Lot awarded, where the tender was divided.')
    amount: float | None = Field(default=None, description='Awarded value.')
    currency: str | None = Field(default=None, description='Currency of the award.')


class BeneficialOwnerOf(TemporalRelationshipBase):
    """
    Ultimately benefits from or controls the entity, whether or not the holding is
    direct.
    """
    type: Literal["BENEFICIAL_OWNER_OF"] = "BENEFICIAL_OWNER_OF"
    percentage: float | None = Field(default=None, description='Percentage beneficially held.')
    nature_of_control: list[str] = Field(default_factory=list, description='How control is exercised, as reported by the registry.')


class BidOn(RelationshipBase):
    """Submitted a tender in response to the notice."""
    type: Literal["BID_ON"] = "BID_ON"


class Controls(TemporalRelationshipBase):
    """Exercises control by means other than shareholding."""
    type: Literal["CONTROLS"] = "CONTROLS"
    basis: str | None = Field(default=None, description='Basis of control, e.g. voting agreement, golden share.')


class DirectorOf(TemporalRelationshipBase):
    """Serves on the board or as a statutory officer."""
    type: Literal["DIRECTOR_OF"] = "DIRECTOR_OF"
    role: str | None = Field(default=None, description='Role as stated by the registry, e.g. geschäftsführer.')
    appointed_by: str | None = Field(default=None, description='Body that made the appointment.')


class EmployedBy(TemporalRelationshipBase):
    """Works for the entity."""
    type: Literal["EMPLOYED_BY"] = "EMPLOYED_BY"
    job_title: str | None = Field(default=None, description='Job title.')


class FamilyOf(TemporalRelationshipBase):
    """Related by blood, marriage or partnership."""
    type: Literal["FAMILY_OF"] = "FAMILY_OF"
    relationship: str | None = Field(default=None, description='Nature of the tie, e.g. spouse, sibling, parent.')


class HasEmail(TemporalRelationshipBase):
    """Reachable at this address."""
    type: Literal["HAS_EMAIL"] = "HAS_EMAIL"


class HasIdentifier(TemporalRelationshipBase):
    """
    Bears an identifier issued by an authority. Two entities sharing an identifier
    are proposed as duplicate candidates.
    """
    type: Literal["HAS_IDENTIFIER"] = "HAS_IDENTIFIER"


class HasPhone(TemporalRelationshipBase):
    """Reachable on this number."""
    type: Literal["HAS_PHONE"] = "HAS_PHONE"


class HasWebsite(TemporalRelationshipBase):
    """Operates this website."""
    type: Literal["HAS_WEBSITE"] = "HAS_WEBSITE"


class HoldsAccount(TemporalRelationshipBase):
    """Is the account holder."""
    type: Literal["HOLDS_ACCOUNT"] = "HOLDS_ACCOUNT"


class IssuedTender(RelationshipBase):
    """Published the procurement notice as contracting authority."""
    type: Literal["ISSUED_TENDER"] = "ISSUED_TENDER"


class LocatedAt(TemporalRelationshipBase):
    """Physically situated at the address."""
    type: Literal["LOCATED_AT"] = "LOCATED_AT"


class MemberOf(TemporalRelationshipBase):
    """Belongs to an organisation."""
    type: Literal["MEMBER_OF"] = "MEMBER_OF"
    role: str | None = Field(default=None, description='Role within the organisation.')


class Mentions(RelationshipBase):
    """
    The source refers to the entity. This is node-level provenance; claims about
    relationships are carried by the assertion layer.
    """
    type: Literal["MENTIONS"] = "MENTIONS"
    page: int | None = Field(default=None, description='Page or location within the source.')
    quote: str | None = Field(default=None, description='Supporting excerpt.')


class OfficerOf(TemporalRelationshipBase):
    """
    Holds a statutory office that is not a directorship — company secretary and
    equivalents. Kept apart from DIRECTOR_OF because the duties and the significance
    to an investigation differ.
    """
    type: Literal["OFFICER_OF"] = "OFFICER_OF"
    role: str | None = Field(default=None, description='Office held, as stated by the registry.')


class Owns(TemporalRelationshipBase):
    """Holds an ownership interest in an asset or entity."""
    type: Literal["OWNS"] = "OWNS"
    percentage: float | None = Field(default=None, description='Percentage of the holding, where known.')
    share_class: str | None = Field(default=None, description='Class of shares held.')


class ParentOf(TemporalRelationshipBase):
    """Sits directly above another entity in a corporate group."""
    type: Literal["PARENT_OF"] = "PARENT_OF"


class PartyTo(TemporalRelationshipBase):
    """Appears in the proceedings."""
    type: Literal["PARTY_TO"] = "PARTY_TO"
    role: Literal["claimant", "defendant", "witness", "third_party", "prosecutor", "unknown"] | None = Field(default=None, description='Capacity in which they appear.')


class ReceivedBy(RelationshipBase):
    """Is the payee of the transaction."""
    type: Literal["RECEIVED_BY"] = "RECEIVED_BY"


class RegisteredAt(TemporalRelationshipBase):
    """Has its registered or business address here."""
    type: Literal["REGISTERED_AT"] = "REGISTERED_AT"
    address_type: Literal["registered", "trading", "correspondence", "branch"] | None = Field(default=None, description='Which kind of address this is.')


class ResidesAt(TemporalRelationshipBase):
    """Lives at the address."""
    type: Literal["RESIDES_AT"] = "RESIDES_AT"


class SameAs(RelationshipBase):
    """
    Asserts that two nodes denote the same real-world thing. Created as a candidate
    — never automatically confirmed — and resolved by a human in the review queue.
    """
    type: Literal["SAME_AS"] = "SAME_AS"
    status: Literal["candidate", "confirmed", "rejected"] = Field(description='Where the candidate stands.')
    basis: str | None = Field(default=None, description='Why the match was proposed, e.g. shared LEI.')
    decided_by: str | None = Field(default=None, description='Who confirmed or rejected it.')
    decided_at: datetime | None = Field(default=None, description='When the decision was taken.')


class Sent(RelationshipBase):
    """Is the payer of the transaction."""
    type: Literal["SENT"] = "SENT"


class SubjectTo(TemporalRelationshipBase):
    """Named on the sanctions listing."""
    type: Literal["SUBJECT_TO"] = "SUBJECT_TO"


# ----------------------------------------------------------------------
# Registries
# ----------------------------------------------------------------------

AnyEntity = Address | ApiRecord | BankAccount | Company | Document | EmailAddress | Identifier | LegalCase | NewsArticle | Organisation | Person | PhoneNumber | PublicTender | RealEstate | SanctionsListing | Transaction | Vessel | WebPage | Website

AnyRelationship = AssociateOf | Awarded | BeneficialOwnerOf | BidOn | Controls | DirectorOf | EmployedBy | FamilyOf | HasEmail | HasIdentifier | HasPhone | HasWebsite | HoldsAccount | IssuedTender | LocatedAt | MemberOf | Mentions | OfficerOf | Owns | ParentOf | PartyTo | ReceivedBy | RegisteredAt | ResidesAt | SameAs | Sent | SubjectTo

ENTITY_MODELS: dict[str, type[EntityBase]] = {
    "Address": Address,
    "ApiRecord": ApiRecord,
    "BankAccount": BankAccount,
    "Company": Company,
    "Document": Document,
    "EmailAddress": EmailAddress,
    "Identifier": Identifier,
    "LegalCase": LegalCase,
    "NewsArticle": NewsArticle,
    "Organisation": Organisation,
    "Person": Person,
    "PhoneNumber": PhoneNumber,
    "PublicTender": PublicTender,
    "RealEstate": RealEstate,
    "SanctionsListing": SanctionsListing,
    "Transaction": Transaction,
    "Vessel": Vessel,
    "WebPage": WebPage,
    "Website": Website,
}

RELATIONSHIP_MODELS: dict[str, type[RelationshipBase]] = {
    "ASSOCIATE_OF": AssociateOf,
    "AWARDED": Awarded,
    "BENEFICIAL_OWNER_OF": BeneficialOwnerOf,
    "BID_ON": BidOn,
    "CONTROLS": Controls,
    "DIRECTOR_OF": DirectorOf,
    "EMPLOYED_BY": EmployedBy,
    "FAMILY_OF": FamilyOf,
    "HAS_EMAIL": HasEmail,
    "HAS_IDENTIFIER": HasIdentifier,
    "HAS_PHONE": HasPhone,
    "HAS_WEBSITE": HasWebsite,
    "HOLDS_ACCOUNT": HoldsAccount,
    "ISSUED_TENDER": IssuedTender,
    "LOCATED_AT": LocatedAt,
    "MEMBER_OF": MemberOf,
    "MENTIONS": Mentions,
    "OFFICER_OF": OfficerOf,
    "OWNS": Owns,
    "PARENT_OF": ParentOf,
    "PARTY_TO": PartyTo,
    "RECEIVED_BY": ReceivedBy,
    "REGISTERED_AT": RegisteredAt,
    "RESIDES_AT": ResidesAt,
    "SAME_AS": SameAs,
    "SENT": Sent,
    "SUBJECT_TO": SubjectTo,
}
