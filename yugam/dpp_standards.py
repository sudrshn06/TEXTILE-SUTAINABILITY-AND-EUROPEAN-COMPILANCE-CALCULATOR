"""
OpenEPCIS-Aligned Digital Product Passport (DPP) Standards Mapping.

Provides a standards-compliant JSON-LD mapping layer from CHAKRA-AI's signed
textile Digital Product Passport records into the official OpenEPCIS DPP-Ready
and GS1 Web Vocabulary namespaces.

Official Reference Ontologies & Contexts:
  - GS1 Web Vocabulary: https://ref.gs1.org/voc/
  - OpenEPCIS Common Core: https://ref.openepcis.io/extensions/common/core/
  - OpenEPCIS EU Textile: https://ref.openepcis.io/extensions/eu/textile/
  - Schema.org: https://schema.org/

Technical readiness only:
This mapping does not claim official EU certification, ESPR approval, or GS1 certification.
Authoritative internal passport, Ed25519 digital signatures, and database logic remain unchanged.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Official Dereferenceable JSON-LD Contexts
OPENEPCIS_DPP_CORE_CONTEXT = "https://ref.openepcis.io/extensions/common/core/dpp-core-context.jsonld"
OPENEPCIS_TEXTILE_CONTEXT = "https://ref.openepcis.io/extensions/eu/textile/textile-context.jsonld"

# Official Namespace URIs
GS1_VOCAB_NS = "https://ref.gs1.org/voc/"
OPENEPCIS_CORE_NS = "https://ref.openepcis.io/extensions/common/core/"
OPENEPCIS_TEXTILE_NS = "https://ref.openepcis.io/extensions/eu/textile/"
SCHEMA_ORG_NS = "https://schema.org/"
CHAKRA_EXT_NS = "https://chakra-ai.org/ns/dpp#"

MAPPING_PROFILE = "OpenEPCIS DPP-Ready / Textile"
MAPPING_STATUS = "technical_readiness_only"
DISCLAIMER = (
    "Standards-aligned DPP-ready representation mapped from independently reviewed CHAKRA-AI "
    "record. Technical readiness only; not official EU, ESPR, or GS1 certification."
)

# Explicit allow-list of standard terms used in mapping
STANDARDS_ALLOW_LIST = frozenset({
    # GS1 Web Vocabulary classes & properties
    "gs1:Product",
    "gs1:Organization",
    "gs1:PostalAddress",
    "gs1:QuantitativeValue",
    "gs1:batchNumber",
    "gs1:netWeight",
    "gs1:textileMaterialContent",
    "gs1:manufacturer",
    "gs1:organizationName",
    "gs1:address",
    "gs1:addressRegion",
    "gs1:addressCountry",
    "gs1:value",
    "gs1:unitCode",
    # Schema.org classes & properties
    "schema:Product",
    "schema:identifier",
    "schema:name",
    "schema:dateCreated",
})


def map_passport_to_openepcis_jsonld(passport: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform an internal CHAKRA-AI passport dictionary into a standards-aligned JSON-LD document.

    Follows GS1-first vocabulary precedence for product, textile material, and organization attributes.
    Preserves all CHAKRA-specific environmental, scoring, and cryptographic metadata under chakra: extensions.
    Source fidelity is preserved without fabricating missing values.
    """
    if not passport:
        return {}

    passport_id = passport.get("passport_id")
    calculation_id = passport.get("calculation_id")
    issued_ts = passport.get("issued_at")
    issue_date_iso = None
    if issued_ts:
        try:
            issue_date_iso = datetime.fromtimestamp(int(issued_ts), tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            issue_date_iso = None

    weight_kg = passport.get("weight_kg")
    fiber_name = passport.get("fiber")
    factory = passport.get("factory")
    batch_state = passport.get("batch_state")
    carbon_intensity = passport.get("carbon_intensity")
    data_quality_grade = passport.get("data_quality_grade")
    operational_status = passport.get("operational_status")
    screening_boundary = passport.get("screening_boundary")
    factor_provenance = passport.get("factor_provenance") or {}
    espr_dpp_readiness = passport.get("espr_dpp_readiness") or {}
    signature = passport.get("signature")
    verification_url = passport.get("verification_url")
    revoked = passport.get("revoked", False)
    revocation_reason = passport.get("revocation_reason")

    # Construct JSON-LD Document using official contexts
    jsonld_doc: Dict[str, Any] = {
        "@context": [
            OPENEPCIS_DPP_CORE_CONTEXT,
            OPENEPCIS_TEXTILE_CONTEXT,
            {
                "gs1": GS1_VOCAB_NS,
                "oec": OPENEPCIS_CORE_NS,
                "eutex": OPENEPCIS_TEXTILE_NS,
                "schema": SCHEMA_ORG_NS,
                "chakra": CHAKRA_EXT_NS,
                "xsd": "http://www.w3.org/2001/XMLSchema#",
            },
        ],
        "@type": ["gs1:Product", "schema:Product"],
    }

    if passport_id:
        jsonld_doc["schema:identifier"] = str(passport_id)

    if calculation_id:
        jsonld_doc["gs1:batchNumber"] = str(calculation_id)

    if fiber_name or passport_id:
        jsonld_doc["schema:name"] = f"Textile Batch ({fiber_name or 'Textile'})"

    if issue_date_iso:
        jsonld_doc["schema:dateCreated"] = issue_date_iso

    # Product Net Mass / Net Weight (GS1 Standard: gs1:netWeight)
    # CHAKRA weight_kg represents textile batch mass excluding packaging
    if weight_kg is not None:
        jsonld_doc["gs1:netWeight"] = {
            "@type": "gs1:QuantitativeValue",
            "gs1:value": float(weight_kg),
            "gs1:unitCode": "KGM",
        }

    # Textile Material Content (GS1 Standard: gs1:textileMaterialContent)
    # CHAKRA stores a factual material description without fabricated structured measurements
    if fiber_name:
        jsonld_doc["gs1:textileMaterialContent"] = str(fiber_name)

    # Manufacturer / Economic Operator (GS1 Standard: gs1:manufacturer -> gs1:Organization)
    if factory or batch_state:
        org_entry: Dict[str, Any] = {
            "@type": "gs1:Organization",
        }
        if factory:
            org_entry["gs1:organizationName"] = str(factory)
        if batch_state:
            org_entry["gs1:address"] = {
                "@type": "gs1:PostalAddress",
                "gs1:addressRegion": str(batch_state),
                "gs1:addressCountry": "IN",
            }
        jsonld_doc["gs1:manufacturer"] = org_entry

    # CHAKRA Domain Extensions (Specific platform metrics & screening evidence)
    chakra_extensions: Dict[str, Any] = {}
    if carbon_intensity is not None:
        chakra_extensions["carbonIntensity"] = {
            "value": float(carbon_intensity),
            "unit": "kg CO2e/kg",
            "screeningBoundary": screening_boundary or "Cradle-to-Gate",
        }
    if data_quality_grade:
        chakra_extensions["dataQualityGrade"] = str(data_quality_grade)
    if operational_status:
        chakra_extensions["operationalStatus"] = str(operational_status)
    if passport.get("chakra_score") is not None:
        chakra_extensions["chakraScore"] = passport.get("chakra_score")
    if passport.get("bharat_score") is not None:
        chakra_extensions["bharatScore"] = passport.get("bharat_score")
    if passport.get("score_label"):
        chakra_extensions["scoreLabel"] = passport.get("score_label")
    if passport.get("failed_stage_count") is not None:
        chakra_extensions["failedStageCount"] = passport.get("failed_stage_count")
    if passport.get("failed_stages"):
        chakra_extensions["failedStages"] = passport.get("failed_stages")
    if factor_provenance:
        chakra_extensions["factorProvenance"] = factor_provenance
    if espr_dpp_readiness:
        chakra_extensions["esprDppReadiness"] = espr_dpp_readiness
    if passport.get("document_type"):
        chakra_extensions["documentType"] = passport.get("document_type")
    if passport.get("claim"):
        chakra_extensions["claim"] = passport.get("claim")

    if chakra_extensions:
        jsonld_doc["chakra:extensions"] = chakra_extensions

    # Cryptographic Attestation Metadata
    jsonld_doc["chakra:cryptographicProof"] = {
        "@type": "chakra:Ed25519SignatureProof",
        "chakra:signatureAlgorithm": "Ed25519",
        "chakra:signatureValue": signature,
        "chakra:verificationUrl": verification_url,
        "chakra:auditStatus": "independently_reviewed",
        "chakra:issuerRole": passport.get("issuer_role"),
        "chakra:isRevoked": bool(revoked),
        "chakra:revocationReason": revocation_reason if revoked else None,
    }

    # Standards Profile Metadata
    jsonld_doc["chakra:standardsProfile"] = {
        "profile": MAPPING_PROFILE,
        "status": MAPPING_STATUS,
        "contexts": [OPENEPCIS_DPP_CORE_CONTEXT, OPENEPCIS_TEXTILE_CONTEXT],
        "ontologyNamespaces": {
            "gs1": GS1_VOCAB_NS,
            "oec": OPENEPCIS_CORE_NS,
            "eutex": OPENEPCIS_TEXTILE_NS,
            "schema": SCHEMA_ORG_NS,
            "chakra": CHAKRA_EXT_NS,
        },
        "disclaimer": DISCLAIMER,
    }

    return jsonld_doc


def get_standards_mapping_payload(passport: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrap the JSON-LD representation into the API standards_mapping object.
    Never fails caller.
    """
    try:
        representation = map_passport_to_openepcis_jsonld(passport)
        return {
            "profile": MAPPING_PROFILE,
            "status": MAPPING_STATUS,
            "representation": representation,
            "disclaimer": DISCLAIMER,
        }
    except Exception as ex:
        logger.warning("OpenEPCIS standards mapping failed: %s", ex)
        return {
            "profile": MAPPING_PROFILE,
            "status": "unavailable",
            "error": "Standards mapping is temporarily unavailable.",
            "disclaimer": DISCLAIMER,
        }
