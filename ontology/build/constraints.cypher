// Generated from ontology/ontology.yaml by `sla-ontology generate`.
// Do not edit by hand: edit the ontology and regenerate.
// Ontology version: 0.2.0

// --- Identity -----------------------------------------------------
CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (n:Thing) REQUIRE n.id IS UNIQUE;

// Every node records which ontology version it was written under, so a
// later migration can find the nodes it still has to convert.
CREATE INDEX entity_ontology_version IF NOT EXISTS
FOR (n:Thing) ON (n.ontology_version);

// --- Provenance ---------------------------------------------------
CREATE CONSTRAINT assertion_id_unique IF NOT EXISTS
FOR (a:Assertion) REQUIRE a.id IS UNIQUE;

CREATE INDEX assertion_predicate IF NOT EXISTS
FOR (a:Assertion) ON (a.predicate);

CREATE INDEX assertion_status IF NOT EXISTS
FOR (a:Assertion) ON (a.status);

// --- Canonical nodes ----------------------------------------------
// One node per distinct key, so that two entities sharing a phone
// number or an identifier are attached to the same node and duplicate
// detection is a single hop.
CREATE CONSTRAINT email_address_canonical IF NOT EXISTS
FOR (n:EmailAddress) REQUIRE n.address IS UNIQUE;

CREATE CONSTRAINT identifier_canonical IF NOT EXISTS
FOR (n:Identifier) REQUIRE (n.scheme, n.value) IS UNIQUE;

CREATE CONSTRAINT phone_number_canonical IF NOT EXISTS
FOR (n:PhoneNumber) REQUIRE n.e164 IS UNIQUE;

CREATE CONSTRAINT website_canonical IF NOT EXISTS
FOR (n:Website) REQUIRE n.domain IS UNIQUE;

// --- Property indexes ---------------------------------------------
CREATE INDEX address_city IF NOT EXISTS
FOR (n:Address) ON (n.city);

CREATE INDEX address_country IF NOT EXISTS
FOR (n:Address) ON (n.country);

CREATE INDEX address_name IF NOT EXISTS
FOR (n:Address) ON (n.name);

CREATE INDEX api_record_content_hash IF NOT EXISTS
FOR (n:ApiRecord) ON (n.content_hash);

CREATE INDEX api_record_name IF NOT EXISTS
FOR (n:ApiRecord) ON (n.name);

CREATE INDEX api_record_provider IF NOT EXISTS
FOR (n:ApiRecord) ON (n.provider);

CREATE INDEX bank_account_iban IF NOT EXISTS
FOR (n:BankAccount) ON (n.iban);

CREATE INDEX bank_account_name IF NOT EXISTS
FOR (n:BankAccount) ON (n.name);

CREATE INDEX company_jurisdiction IF NOT EXISTS
FOR (n:Company) ON (n.jurisdiction);

CREATE INDEX company_name IF NOT EXISTS
FOR (n:Company) ON (n.name);

CREATE INDEX company_registration_number IF NOT EXISTS
FOR (n:Company) ON (n.registration_number);

CREATE INDEX document_content_hash IF NOT EXISTS
FOR (n:Document) ON (n.content_hash);

CREATE INDEX document_name IF NOT EXISTS
FOR (n:Document) ON (n.name);

CREATE INDEX email_address_domain IF NOT EXISTS
FOR (n:EmailAddress) ON (n.domain);

CREATE INDEX email_address_name IF NOT EXISTS
FOR (n:EmailAddress) ON (n.name);

CREATE INDEX identifier_name IF NOT EXISTS
FOR (n:Identifier) ON (n.name);

CREATE INDEX legal_case_case_number IF NOT EXISTS
FOR (n:LegalCase) ON (n.case_number);

CREATE INDEX legal_case_name IF NOT EXISTS
FOR (n:LegalCase) ON (n.name);

CREATE INDEX news_article_content_hash IF NOT EXISTS
FOR (n:NewsArticle) ON (n.content_hash);

CREATE INDEX news_article_name IF NOT EXISTS
FOR (n:NewsArticle) ON (n.name);

CREATE INDEX news_article_url IF NOT EXISTS
FOR (n:NewsArticle) ON (n.url);

CREATE INDEX organisation_jurisdiction IF NOT EXISTS
FOR (n:Organisation) ON (n.jurisdiction);

CREATE INDEX organisation_name IF NOT EXISTS
FOR (n:Organisation) ON (n.name);

CREATE INDEX organisation_registration_number IF NOT EXISTS
FOR (n:Organisation) ON (n.registration_number);

CREATE INDEX person_last_name IF NOT EXISTS
FOR (n:Person) ON (n.last_name);

CREATE INDEX person_name IF NOT EXISTS
FOR (n:Person) ON (n.name);

CREATE INDEX phone_number_name IF NOT EXISTS
FOR (n:PhoneNumber) ON (n.name);

CREATE INDEX public_tender_name IF NOT EXISTS
FOR (n:PublicTender) ON (n.name);

CREATE INDEX public_tender_reference IF NOT EXISTS
FOR (n:PublicTender) ON (n.reference);

CREATE INDEX real_estate_cadastral_reference IF NOT EXISTS
FOR (n:RealEstate) ON (n.cadastral_reference);

CREATE INDEX real_estate_name IF NOT EXISTS
FOR (n:RealEstate) ON (n.name);

CREATE INDEX sanctions_listing_authority IF NOT EXISTS
FOR (n:SanctionsListing) ON (n.authority);

CREATE INDEX sanctions_listing_name IF NOT EXISTS
FOR (n:SanctionsListing) ON (n.name);

CREATE INDEX transaction_name IF NOT EXISTS
FOR (n:Transaction) ON (n.name);

CREATE INDEX vessel_imo_number IF NOT EXISTS
FOR (n:Vessel) ON (n.imo_number);

CREATE INDEX vessel_name IF NOT EXISTS
FOR (n:Vessel) ON (n.name);

CREATE INDEX web_page_content_hash IF NOT EXISTS
FOR (n:WebPage) ON (n.content_hash);

CREATE INDEX web_page_name IF NOT EXISTS
FOR (n:WebPage) ON (n.name);

CREATE INDEX web_page_url IF NOT EXISTS
FOR (n:WebPage) ON (n.url);

CREATE INDEX website_name IF NOT EXISTS
FOR (n:Website) ON (n.name);
