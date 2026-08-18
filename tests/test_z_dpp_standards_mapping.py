import json
from unittest.mock import patch

from yugam.dpp_standards import (
    map_passport_to_openepcis_jsonld,
    get_standards_mapping_payload,
    OPENEPCIS_DPP_CORE_CONTEXT,
    OPENEPCIS_TEXTILE_CONTEXT,
    OPENEPCIS_CORE_NS,
    OPENEPCIS_TEXTILE_NS,
    GS1_VOCAB_NS,
    SCHEMA_ORG_NS,
    CHAKRA_EXT_NS,
    MAPPING_PROFILE,
    MAPPING_STATUS,
    STANDARDS_ALLOW_LIST,
)


def _mock_passport_dict(**overrides):
    base = {
        "passport_id": "DPP-1234567890ABCDEF1234",
        "calculation_id": "CAL-A1B2C3D4E5F6A1B2C3D4E5F6",
        "factory": "Coimbatore Sustainable Spinning Mills",
        "batch_state": "Tamil Nadu",
        "fiber": "Recycled Cotton",
        "weight_kg": 5000.0,
        "carbon_intensity": 2.85,
        "chakra_score": 88.5,
        "bharat_score": 88.5,
        "score_label": "CHAKRA Compliant",
        "operational_status": "PASS",
        "failed_stage_count": 0,
        "failed_stages": [],
        "issued_at": 1755500000,
        "issuer_role": "Compliance Auditor",
        "document_type": "CHAKRA-AI Signed Digital Product Passport Readiness Record",
        "data_quality_grade": "A",
        "factor_provenance": {
            "grid": {"value": 0.710, "source": "CEA Database v21.0"},
            "fiber": {"value": 2.10, "source": "Higgs Index 2024"},
        },
        "screening_boundary": "Cradle-to-Gate (Stages 1-5)",
        "espr_dpp_readiness": {
            "status": "technical_readiness_only",
            "product_specific_espr_rules_verified": False,
        },
        "claim": "Independently reviewed factual CHAKRA calculation record.",
        "signature": "mock_signature_b64_string",
        "verification_url": "http://testserver/api/v2/passports/DPP-1234567890ABCDEF1234/verify",
        "revoked": False,
        "revocation_reason": None,
    }
    base.update(overrides)
    return base


def _extract_all_namespaced_keys_and_types(obj, results=None):
    if results is None:
        results = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if ":" in k and not k.startswith("@"):
                results.add(k)
            if k == "@type":
                types = v if isinstance(v, list) else [v]
                for t in types:
                    if ":" in t:
                        results.add(t)
            _extract_all_namespaced_keys_and_types(v, results)
    elif isinstance(obj, list):
        for item in obj:
            _extract_all_namespaced_keys_and_types(item, results)
    return results


def test_official_namespaces_and_contexts_present():
    passport = _mock_passport_dict()
    jsonld = map_passport_to_openepcis_jsonld(passport)

    context = jsonld["@context"]
    assert isinstance(context, list)

    # Verify official JSON-LD context URLs
    assert OPENEPCIS_DPP_CORE_CONTEXT in context
    assert OPENEPCIS_TEXTILE_CONTEXT in context
    assert OPENEPCIS_DPP_CORE_CONTEXT == "https://ref.openepcis.io/extensions/common/core/dpp-core-context.jsonld"
    assert OPENEPCIS_TEXTILE_CONTEXT == "https://ref.openepcis.io/extensions/eu/textile/textile-context.jsonld"

    # Verify official namespace URIs
    ns_dict = [c for c in context if isinstance(c, dict)][0]
    assert ns_dict["oec"] == "https://ref.openepcis.io/extensions/common/core/"
    assert ns_dict["eutex"] == "https://ref.openepcis.io/extensions/eu/textile/"
    assert ns_dict["gs1"] == "https://ref.gs1.org/voc/"
    assert ns_dict["schema"] == "https://schema.org/"
    assert ns_dict["chakra"] == "https://chakra-ai.org/ns/dpp#"

    # Verify deprecated/invented URIs are completely absent
    serialized = json.dumps(jsonld)
    assert "https://openepcis.io/dpp/core/v1#" not in serialized
    assert "https://openepcis.io/dpp/textile/v1#" not in serialized
    assert "openepcis.io/dpp/" not in serialized


def test_weight_and_textile_material_gs1_semantics():
    passport = _mock_passport_dict()
    jsonld = map_passport_to_openepcis_jsonld(passport)

    # 1. Weight: Must be gs1:netWeight (textile batch mass excluding packaging)
    assert "gs1:netWeight" in jsonld
    assert "gs1:grossWeight" not in jsonld
    assert jsonld["gs1:netWeight"]["@type"] == "gs1:QuantitativeValue"
    assert jsonld["gs1:netWeight"]["gs1:value"] == 5000.0
    assert jsonld["gs1:netWeight"]["gs1:unitCode"] == "KGM"

    # 2. Textile Material: gs1:textileMaterialContent string literal
    assert jsonld["gs1:textileMaterialContent"] == "Recycled Cotton"
    assert "gs1:textileMaterial" not in jsonld


def test_gs1_and_schema_vocabulary_terms_adhere_to_allow_list():
    passport = _mock_passport_dict()
    jsonld = map_passport_to_openepcis_jsonld(passport)

    all_keys_and_types = _extract_all_namespaced_keys_and_types(jsonld)
    for term in all_keys_and_types:
        prefix = term.split(":")[0]
        if prefix in ("gs1", "schema", "oec", "eutex"):
            assert term in STANDARDS_ALLOW_LIST, f"Term '{term}' is not in STANDARDS_ALLOW_LIST!"


def test_chakra_specific_fields_remain_under_chakra_namespace():
    passport = _mock_passport_dict()
    jsonld = map_passport_to_openepcis_jsonld(passport)

    assert "chakra:extensions" in jsonld
    extensions = jsonld["chakra:extensions"]

    # CHAKRA proprietary metrics must be under extensions, not invented in oec: or eutex:
    assert "carbonIntensity" in extensions
    assert extensions["carbonIntensity"]["value"] == 2.85
    assert extensions["chakraScore"] == 88.5
    assert extensions["bharatScore"] == 88.5
    assert extensions["operationalStatus"] == "PASS"
    assert "factorProvenance" in extensions
    assert "esprDppReadiness" in extensions

    # Cryptographic proof & standards profile
    assert "chakra:cryptographicProof" in jsonld
    assert jsonld["chakra:cryptographicProof"]["chakra:signatureAlgorithm"] == "Ed25519"
    assert "chakra:standardsProfile" in jsonld
    assert jsonld["chakra:standardsProfile"]["profile"] == MAPPING_PROFILE
    assert jsonld["chakra:standardsProfile"]["status"] == MAPPING_STATUS


def test_missing_data_not_fabricated():
    sparse_passport = {
        "passport_id": "DPP-MINIMAL000000000001",
        "calculation_id": "CAL-000000000000000000000001",
    }
    jsonld = map_passport_to_openepcis_jsonld(sparse_passport)
    assert jsonld["schema:identifier"] == "DPP-MINIMAL000000000001"
    assert jsonld["gs1:batchNumber"] == "CAL-000000000000000000000001"
    assert "gs1:manufacturer" not in jsonld
    assert "gs1:netWeight" not in jsonld
    assert "gs1:grossWeight" not in jsonld
    assert "gs1:textileMaterial" not in jsonld
    assert "gs1:textileMaterialContent" not in jsonld
    assert "carbonIntensity" not in jsonld.get("chakra:extensions", {})


def test_get_standards_mapping_payload_resilience():
    payload = get_standards_mapping_payload({})
    assert payload["profile"] == MAPPING_PROFILE
    assert payload["status"] == MAPPING_STATUS

    with patch("yugam.dpp_standards.map_passport_to_openepcis_jsonld", side_effect=RuntimeError("Simulated mapping crash")):
        safe_payload = get_standards_mapping_payload(_mock_passport_dict())
        assert safe_payload["status"] == "unavailable"
        assert safe_payload["error"] == "Standards mapping is temporarily unavailable."


def test_signing_invariance_and_verification():
    from yugam.app import SIGNING_PRIVATE_KEY, SIGNING_PUBLIC_KEY, _canonical_json

    sample_payload = {
        "passport_id": "DPP-TESTSIGN0000000001",
        "calculation_id": "CAL-111122223333444455556666",
        "factory": "Test Mill",
        "carbon_intensity": 3.12,
    }
    canon_bytes = _canonical_json(sample_payload)
    sig = SIGNING_PRIVATE_KEY.sign(canon_bytes)
    SIGNING_PUBLIC_KEY.verify(sig, canon_bytes)

    mapped = map_passport_to_openepcis_jsonld({**sample_payload, "signature": "test"})
    assert mapped["schema:identifier"] == sample_payload["passport_id"]
    assert mapped["gs1:batchNumber"] == sample_payload["calculation_id"]
