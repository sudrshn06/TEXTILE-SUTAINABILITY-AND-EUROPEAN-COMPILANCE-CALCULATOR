import base64
import json
import os
import time
import uuid
import pytest
from fastapi.testclient import TestClient

# Ensure test secrets are present
os.environ["JWT_SECRET"] = "test-jwt-secret-key-for-testing-only-123456"
os.environ["CSRF_SECRET"] = "test-csrf-secret-key-for-testing-only-123456"

from yugam.gs1_digital_link import (
    calculate_gtin_check_digit,
    normalize_gtin,
    validate_gtin,
    validate_batch_lot,
    build_gs1_digital_link,
    get_digital_link_payload,
    GS1_XCHAR_SET,
)
from yugam.dpp_standards import (
    get_standards_mapping_payload,
    map_passport_to_openepcis_jsonld,
    STANDARDS_ALLOW_LIST,
)
import yugam.app as app_mod
from yugam.app import app, _db, _canonical_json, SIGNING_PRIVATE_KEY


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """
    Isolate the SQLite database for GS1 integration tests using pytest's tmp_path.
    Ensures a fresh, temporary test database is created, initialized with the real
    CHAKRA schema via _init_db(), and populated with a deterministic test user.
    """
    test_db = tmp_path / "gs1_test.db"
    monkeypatch.setattr(app_mod, "DB_PATH", test_db)
    app_mod._init_db()

    # Seed deterministic test user
    with app_mod._db() as conn:
        conn.execute(
            "INSERT INTO users(email, name, factory, role, password_hash, active, created_at) VALUES(?,?,?,?,?,1,?)",
            (
                "test_auditor@chakra.local",
                "Test Auditor",
                "Tirupur Eco Unit",
                "Compliance Auditor",
                app_mod.PASSWORD_HASHER.hash("TestPass123!"),
                int(time.time()),
            ),
        )
        user_row = conn.execute("SELECT id, factory FROM users WHERE email='test_auditor@chakra.local'").fetchone()
        user_id = user_row["id"]
        factory = user_row["factory"]

    return {"db_path": test_db, "user_id": user_id, "factory": factory}


# ==============================================================================
# 1. GS1 Check Digit Calculation & Validation Tests
# ==============================================================================

def test_calculate_gtin_check_digit_valid_vectors():
    # Official GS1 check digit test vectors
    # 0952012345678 -> 8 (GTIN-14: 09520123456788)
    assert calculate_gtin_check_digit("0952012345678") == 8
    # GTIN-13: 950110153000 -> 3 (padded: 0950110153000)
    assert calculate_gtin_check_digit("0950110153000") == 3
    # GTIN-12 (UPC-A): 01234567890 -> 5 (padded: 0001234567890)
    assert calculate_gtin_check_digit("0001234567890") == 5
    # GTIN-8: 4012345 -> 5 (padded: 0000004012345)
    assert calculate_gtin_check_digit("0000004012345") == 5
    # GTIN-8: 9501101 -> 1 (padded: 0000009501101)
    assert calculate_gtin_check_digit("0000009501101") == 1


def test_normalize_gtin_formats():
    # GTIN-14 (already 14 digits)
    assert normalize_gtin("09520123456788") == "09520123456788"

    # GTIN-13 (13 digits -> padded to 14)
    assert normalize_gtin("9501101530003") == "09501101530003"

    # GTIN-12 (12 digits -> padded to 14)
    assert normalize_gtin("012345678905") == "00012345678905"

    # GTIN-8 (8 digits -> padded to 14)
    assert normalize_gtin("40123455") == "00000040123455"
    assert normalize_gtin("95011011") == "00000095011011"


def test_validate_gtin_invalid_check_digit():
    # Last digit is wrong (should be 8, supplied 9)
    valid, norm, err = validate_gtin("09520123456789")
    assert not valid
    assert norm is None
    assert "check digit" in err.lower()


def test_validate_gtin_non_numeric_and_invalid_lengths():
    # Alphanumeric
    valid, norm, err = validate_gtin("0952012345678A")
    assert not valid
    assert "numeric" in err

    # Special characters
    valid, norm, err = validate_gtin("09520-1234-567")
    assert not valid

    # Inserted hyphen in otherwise valid GTIN (must NOT be silently cleaned)
    valid, norm, err = validate_gtin("0952012345678-8")
    assert not valid
    assert "numeric" in err

    # Inserted space in otherwise valid GTIN (must NOT be silently cleaned)
    valid, norm, err = validate_gtin("0952012345678 8")
    assert not valid
    assert "numeric" in err

    # Leading/trailing spaces (must NOT be silently cleaned)
    valid, norm, err = validate_gtin(" 09520123456788")
    assert not valid
    assert "numeric" in err
    valid, norm, err = validate_gtin("09520123456788 ")
    assert not valid
    assert "numeric" in err

    # Invalid length (7 digits)
    valid, norm, err = validate_gtin("1234567")
    assert not valid
    assert "length" in err.lower()

    # Invalid length (15 digits)
    valid, norm, err = validate_gtin("009520123456788")
    assert not valid

    # Empty / None
    valid, norm, err = validate_gtin(None)
    assert not valid
    valid, norm, err = validate_gtin("   ")
    assert not valid


def test_normalize_gtin_ascii_digits_only_regression():
    # Valid ASCII GTIN-14 must be accepted
    assert normalize_gtin("09501101530003") == "09501101530003"
    valid, norm, err = validate_gtin("09501101530003")
    assert valid
    assert norm == "09501101530003"
    assert err is None

    # Arabic-Indic non-ASCII digits must be rejected
    with pytest.raises(ValueError, match="numeric digits"):
        normalize_gtin("٠٩٥٠١١٠١٥٣٠٠٠٣")
    valid, norm, err = validate_gtin("٠٩٥٠١١٠١٥٣٠٠٠٣")
    assert not valid
    assert norm is None
    assert "numeric" in err

    # Fullwidth non-ASCII digits must be rejected
    with pytest.raises(ValueError, match="numeric digits"):
        normalize_gtin("０９５０１１０１５３０００３")
    valid, norm, err = validate_gtin("０９５０１１０１５３０００３")
    assert not valid
    assert norm is None
    assert "numeric" in err

    # Mixed ASCII and Unicode digits must be rejected
    with pytest.raises(ValueError, match="numeric digits"):
        normalize_gtin("095011015300０3")
    valid, norm, err = validate_gtin("095011015300０3")
    assert not valid
    assert norm is None
    assert "numeric" in err


# ==============================================================================
# 2. AI 10 Batch/Lot Validation (1*20 XCHAR) Tests
# ==============================================================================

def test_validate_batch_lot_valid_inputs():
    # Standard alphanumeric
    valid, clean, err = validate_batch_lot("ABC123")
    assert valid
    assert clean == "ABC123"
    assert err is None

    # Hyphens, dots, underscores, slashes (all in XCHAR 82-character subset)
    valid, clean, err = validate_batch_lot("LOT-2025/08_TN.1")
    assert valid
    assert clean == "LOT-2025/08_TN.1"

    # Forward slash
    valid, clean, err = validate_batch_lot("LOT/42")
    assert valid
    assert clean == "LOT/42"

    # Exact 20 characters length (maximum allowed)
    valid, clean, err = validate_batch_lot("12345678901234567890")
    assert valid
    assert len(clean) == 20

    # Optional / None / Empty
    valid, clean, err = validate_batch_lot(None)
    assert valid
    assert clean is None
    valid, clean, err = validate_batch_lot("")
    assert valid
    assert clean is None


def test_validate_batch_lot_invalid_inputs():
    # Spaces are not in GS1 XCHAR 82-character subset
    valid, clean, err = validate_batch_lot("LOT 42")
    assert not valid
    assert "outside GS1 XCHAR" in err

    # Hash '#' is not in GS1 XCHAR 82-character subset
    valid, clean, err = validate_batch_lot("LOT#42")
    assert not valid
    assert "outside GS1 XCHAR" in err

    # Exceeds 20 characters
    valid, clean, err = validate_batch_lot("123456789012345678901")
    assert not valid
    assert "exceeds 20 characters" in err

    # Unicode / Emoji
    valid, clean, err = validate_batch_lot("LOT\U0001F680")
    assert not valid
    assert "outside GS1 XCHAR" in err

    # Non-ASCII script (Tamil)
    valid, clean, err = validate_batch_lot("\u0ba4\u0bae\u0bbf\u0bb4\u0bcd123")
    assert not valid
    assert "outside GS1 XCHAR" in err


# ==============================================================================
# 3. GS1 Digital Link URI Building Tests
# ==============================================================================

def test_build_gs1_digital_link_no_batch():
    uri = build_gs1_digital_link("09520123456788", base_url="https://example.com")
    assert uri == "https://example.com/id/01/09520123456788"


def test_build_gs1_digital_link_with_batch():
    uri = build_gs1_digital_link("09520123456788", batch_lot="BATCH-2025-A", base_url="https://example.com")
    assert uri == "https://example.com/id/01/09520123456788/10/BATCH-2025-A"


def test_build_gs1_digital_link_percent_encodes_slash():
    uri = build_gs1_digital_link("09520123456788", batch_lot="LOT/42", base_url="https://example.com")
    # Slash should be percent-encoded as %2F in AI 10 path segment
    assert uri == "https://example.com/id/01/09520123456788/10/LOT%2F42"


def test_build_gs1_digital_link_rejects_invalid_batch():
    with pytest.raises(ValueError, match="Invalid AI 10 Batch/Lot"):
        build_gs1_digital_link("09520123456788", batch_lot="LOT 42", base_url="https://example.com")


def test_get_digital_link_payload_structure():
    payload = get_digital_link_payload("09520123456788", batch_lot="LOT-101", base_url="https://chakra.example.com")
    assert payload is not None
    assert payload["gtin"] == "09520123456788"
    assert payload["batch_lot"] == "LOT-101"
    assert payload["digital_link_uri"] == "https://chakra.example.com/id/01/09520123456788/10/LOT-101"
    assert "GS1 Digital Link URI Syntax v1.7.0" in payload["syntax_standard"]
    assert "disclaimer" in payload


# ==============================================================================
# 4. Standards Mapping Semantic Verification Tests
# ==============================================================================

def test_standards_mapping_gs1_gtin_and_no_gs1_lot_number():
    passport = {
        "passport_id": "DPP-TEST1234567890123456",
        "calculation_id": "CALC-2025-001",
        "factory": "Coimbatore Mills",
        "fiber": "Recycled Cotton",
        "weight_kg": 5000.0,
        "carbon_intensity": 1.85,
        "chakra_score": 88.5,
        "operational_status": "PASS",
        "failed_stage_count": 0,
        "issued_at": 1740000000,
        "issuer_role": "Compliance Auditor",
        "verification_url": "https://example.com/api/v2/passports/DPP-TEST1234567890123456/verify",
        "gtin": "09520123456788",
        "batch_lot": "LOT/42",
    }
    mapping = get_standards_mapping_payload(passport)
    rep = mapping["representation"]

    # 1. gs1:gtin exists officially and is included
    assert rep["gs1:gtin"] == "09520123456788"
    assert "gs1:gtin" in STANDARDS_ALLOW_LIST

    # 2. gs1:lotNumber does NOT exist officially and is excluded
    assert "gs1:lotNumber" not in rep
    assert "gs1:lotNumber" not in STANDARDS_ALLOW_LIST

    # 3. Batch metadata preserved under chakra:extensions
    ext = rep["chakra:extensions"]
    assert ext["batchLot"] == "LOT/42"
    assert "gs1DigitalLink" in ext
    assert ext["gs1DigitalLink"]["gtin"] == "09520123456788"
    assert ext["gs1DigitalLink"]["batch_lot"] == "LOT/42"


def test_standards_mapping_no_fabricated_gtin_when_absent():
    passport = {
        "passport_id": "DPP-TEST1234567890123456",
        "calculation_id": "CALC-2025-001",
        "factory": "Coimbatore Mills",
        "fiber": "Recycled Cotton",
        "weight_kg": 5000.0,
        "carbon_intensity": 1.85,
        "chakra_score": 88.5,
        "operational_status": "PASS",
        "failed_stage_count": 0,
        "issued_at": 1740000000,
        "issuer_role": "Compliance Auditor",
        "verification_url": "https://example.com/api/v2/passports/DPP-TEST1234567890123456/verify",
        # NO gtin or batch_lot supplied
    }
    mapping = get_standards_mapping_payload(passport)
    rep = mapping["representation"]

    assert "gs1:gtin" not in rep
    assert "gs1:lotNumber" not in rep
    assert "gs1DigitalLink" not in rep.get("chakra:extensions", {})


# ==============================================================================
# 5. Routing Integration & Encoded-Slash Resolution Tests
# ==============================================================================

def test_resolve_gs1_digital_link_with_encoded_slash(isolated_db):
    """
    Test that /id/01/{gtin}/10/LOT%2F42 safely routes and decodes to 'LOT/42'
    without splitting into accidental path segments.
    """
    with TestClient(app) as client:
        # 1. Seed a test passport in the isolated DB with GTIN and a batch containing a slash
        passport_id = "DPP-" + uuid.uuid4().hex[:20].upper()
        calc_id = "CALC-" + uuid.uuid4().hex[:12].upper()
        issued_at = int(time.time())

        payload = {
            "passport_id": passport_id,
            "calculation_id": calc_id,
            "factory": isolated_db["factory"],
            "batch_state": "Tamil Nadu",
            "fiber": "Recycled Cotton",
            "weight_kg": 5000.0,
            "carbon_intensity": 2.1,
            "chakra_score": 85.0,
            "bharat_score": 85.0,
            "score_label": "High",
            "operational_status": "PASS",
            "failed_stage_count": 0,
            "failed_stages": [],
            "issued_at": issued_at,
            "issuer_role": "Compliance Auditor",
            "gtin": "09520123456788",
            "batch_lot": "LOT/42",
        }
        signature = SIGNING_PRIVATE_KEY.sign(_canonical_json(payload))
        signature_b64 = base64.urlsafe_b64encode(signature).decode("ascii")

        with _db() as conn:
            user_id = isolated_db["user_id"]
            factory = isolated_db["factory"]

            conn.execute("""
                INSERT OR REPLACE INTO calculations(calculation_id, user_id, factory, created_at, input_json, result_json, review_status)
                VALUES(?,?,?,?,?,?,?)
            """, (calc_id, user_id, factory, issued_at, json.dumps({"weight_kg": 5000, "state": "Tamil Nadu", "gtin": "09520123456788", "batch_lot": "LOT/42"}), json.dumps({"data": {}}), "approved"))

            conn.execute("""
                INSERT INTO passports(passport_id, calculation_id, issuer_user_id, issued_at, payload_json, signature_b64)
                VALUES(?,?,?,?,?,?)
            """, (passport_id, calc_id, user_id, issued_at, json.dumps(payload), signature_b64))

        # 2. Query with percent-encoded slash in batch: /id/01/09520123456788/10/LOT%2F42
        resp = client.get("/id/01/09520123456788/10/LOT%2F42")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "located"
        assert data["gtin"] == "09520123456788"
        assert data["batch_lot"] == "LOT/42"
        assert data["passport_id"] == passport_id
        assert passport_id in data["verification_url"]

        # 3. Query without batch: /id/01/09520123456788
        resp_gtin_only = client.get("/id/01/09520123456788")
        assert resp_gtin_only.status_code == 200
        assert resp_gtin_only.json()["passport_id"] == passport_id

        # 4. Query with invalid batch containing space -> 400 Bad Request
        resp_invalid_batch = client.get("/id/01/09520123456788/10/LOT%2042")
        assert resp_invalid_batch.status_code == 400
        assert "Invalid AI 10 Batch/Lot" in resp_invalid_batch.json()["detail"]

        # 5. Query with non-matching batch -> 404
        resp_nomatch = client.get("/id/01/09520123456788/10/OTHER-BATCH")
        assert resp_nomatch.status_code == 404


def test_resolve_literal_percent_data_no_double_decoding(isolated_db):
    """
    Regression test: Stored batch 'LOT%2F42' must generate '.../10/LOT%252F42'
    and resolve back to 'LOT%2F42' (NOT double-decoded to 'LOT/42').
    """
    with TestClient(app) as client:
        unique_gtin = "9501101530003"
        norm_gtin = normalize_gtin(unique_gtin)

        # Stored batch with literal percent sequence
        stored_batch = "LOT%2F42"
        generated_uri = build_gs1_digital_link(unique_gtin, batch_lot=stored_batch, base_url="http://testserver")
        assert "/10/LOT%252F42" in generated_uri

        passport_id = "DPP-" + uuid.uuid4().hex[:20].upper()
        calc_id = "CALC-" + uuid.uuid4().hex[:12].upper()
        issued_at = int(time.time())

        payload = {
            "passport_id": passport_id,
            "calculation_id": calc_id,
            "factory": isolated_db["factory"],
            "batch_state": "Tamil Nadu",
            "fiber": "Recycled Cotton",
            "weight_kg": 5000.0,
            "carbon_intensity": 2.1,
            "chakra_score": 85.0,
            "bharat_score": 85.0,
            "score_label": "High",
            "operational_status": "PASS",
            "failed_stage_count": 0,
            "failed_stages": [],
            "issued_at": issued_at,
            "issuer_role": "Compliance Auditor",
            "gtin": unique_gtin,
            "batch_lot": stored_batch,
        }
        signature = SIGNING_PRIVATE_KEY.sign(_canonical_json(payload))
        signature_b64 = base64.urlsafe_b64encode(signature).decode("ascii")

        with _db() as conn:
            user_id = isolated_db["user_id"]
            factory = isolated_db["factory"]

            conn.execute("""
                INSERT OR REPLACE INTO calculations(calculation_id, user_id, factory, created_at, input_json, result_json, review_status)
                VALUES(?,?,?,?,?,?,?)
            """, (calc_id, user_id, factory, issued_at, json.dumps({"weight_kg": 5000, "state": "Tamil Nadu", "gtin": unique_gtin, "batch_lot": stored_batch}), json.dumps({"data": {}}), "approved"))

            conn.execute("""
                INSERT INTO passports(passport_id, calculation_id, issuer_user_id, issued_at, payload_json, signature_b64)
                VALUES(?,?,?,?,?,?)
            """, (passport_id, calc_id, user_id, issued_at, json.dumps(payload), signature_b64))

        # Requesting the generated URI path with %252F in TestClient (which decodes once in ASGI transport)
        resp = client.get(f"/id/01/{unique_gtin}/10/LOT%25252F42")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "located"
        assert data["batch_lot"] == "LOT%2F42"
        assert data["batch_lot"] != "LOT/42"
        assert data["passport_id"] == passport_id


def test_resolve_gtin_only_ambiguity_returns_409(isolated_db):
    """
    When multiple passports share the same GTIN but different batches,
    a GTIN-only lookup must return HTTP 409 Conflict.
    A batch-specific lookup must resolve only the matching record.
    """
    with TestClient(app) as client:
        shared_gtin = "012345678905"
        norm_gtin = normalize_gtin(shared_gtin)
        issued_at = int(time.time())

        user_id = isolated_db["user_id"]
        factory = isolated_db["factory"]

        with _db() as conn:
            # Insert Passport A (LOT-AMBIG-A)
            pid_a = "DPP-" + uuid.uuid4().hex[:20].upper()
            cid_a = "CALC-" + uuid.uuid4().hex[:12].upper()
            payload_a = {
                "passport_id": pid_a, "calculation_id": cid_a, "factory": factory,
                "fiber": "Recycled Cotton", "weight_kg": 1000.0, "issued_at": issued_at,
                "gtin": shared_gtin, "batch_lot": "LOT-AMBIG-A",
            }
            sig_a = base64.urlsafe_b64encode(SIGNING_PRIVATE_KEY.sign(_canonical_json(payload_a))).decode("ascii")
            conn.execute("INSERT OR REPLACE INTO calculations(calculation_id, user_id, factory, created_at, input_json, result_json, review_status) VALUES(?,?,?,?,?,?,?)",
                         (cid_a, user_id, factory, issued_at, json.dumps(payload_a), "{}", "approved"))
            conn.execute("INSERT INTO passports(passport_id, calculation_id, issuer_user_id, issued_at, payload_json, signature_b64) VALUES(?,?,?,?,?,?)",
                         (pid_a, cid_a, user_id, issued_at, json.dumps(payload_a), sig_a))

            # Insert Passport B (LOT-AMBIG-B)
            pid_b = "DPP-" + uuid.uuid4().hex[:20].upper()
            cid_b = "CALC-" + uuid.uuid4().hex[:12].upper()
            payload_b = {
                "passport_id": pid_b, "calculation_id": cid_b, "factory": factory,
                "fiber": "Organic Cotton", "weight_kg": 2000.0, "issued_at": issued_at + 10,
                "gtin": shared_gtin, "batch_lot": "LOT-AMBIG-B",
            }
            sig_b = base64.urlsafe_b64encode(SIGNING_PRIVATE_KEY.sign(_canonical_json(payload_b))).decode("ascii")
            conn.execute("INSERT OR REPLACE INTO calculations(calculation_id, user_id, factory, created_at, input_json, result_json, review_status) VALUES(?,?,?,?,?,?,?)",
                         (cid_b, user_id, factory, issued_at + 10, json.dumps(payload_b), "{}", "approved"))
            conn.execute("INSERT INTO passports(passport_id, calculation_id, issuer_user_id, issued_at, payload_json, signature_b64) VALUES(?,?,?,?,?,?)",
                         (pid_b, cid_b, user_id, issued_at + 10, json.dumps(payload_b), sig_b))

        # 1. GTIN-only request must return HTTP 409 Conflict with generic message
        resp_ambig = client.get(f"/id/01/{shared_gtin}")
        assert resp_ambig.status_code == 409
        assert "Multiple Digital Product Passports exist for this GTIN" in resp_ambig.json()["detail"]
        assert "Supply batch/lot (AI 10)" in resp_ambig.json()["detail"]

        # 2. GTIN + LOT-AMBIG-A resolves only Passport A
        resp_a = client.get(f"/id/01/{shared_gtin}/10/LOT-AMBIG-A")
        assert resp_a.status_code == 200
        assert resp_a.json()["passport_id"] == pid_a
        assert resp_a.json()["batch_lot"] == "LOT-AMBIG-A"

        # 3. GTIN + LOT-AMBIG-B resolves only Passport B
        resp_b = client.get(f"/id/01/{shared_gtin}/10/LOT-AMBIG-B")
        assert resp_b.status_code == 200
        assert resp_b.json()["passport_id"] == pid_b
        assert resp_b.json()["batch_lot"] == "LOT-AMBIG-B"


def test_lcainput_fail_closed_and_max_lengths():
    from yugam.app import LCAInput
    from pydantic import ValidationError

    base_kwargs = {
        "weight_kg": 1000.0,
        "fiber": 1,
        "state": "Tamil Nadu",
        "spin_kwh": 500.0,
        "weave_kwh": 500.0,
        "wet_kwh": 1000.0,
        "wet_heat_kwh": 0.0,
        "water_liters": 10000.0,
        "chemicals_kg": 50.0,
        "sew_kwh": 200.0,
        "waste_kg": 50.0,
        "packaging_kg": 20.0,
    }

    # 1. Valid GTIN and Batch
    inp = LCAInput(**base_kwargs, gtin="09520123456788", batch_lot="BATCH-123")
    assert inp.gtin == "09520123456788"
    assert inp.batch_lot == "BATCH-123"

    # 2. GTIN with hyphen must be rejected
    with pytest.raises(ValidationError):
        LCAInput(**base_kwargs, gtin="0952012345678-8")

    # 3. GTIN with space must be rejected
    with pytest.raises(ValidationError):
        LCAInput(**base_kwargs, gtin="0952012345678 8")

    # 4. GTIN > 14 characters must be rejected
    with pytest.raises(ValidationError):
        LCAInput(**base_kwargs, gtin="009520123456788")

    # 5. Batch/Lot with space must be rejected
    with pytest.raises(ValidationError):
        LCAInput(**base_kwargs, batch_lot="BATCH 123")

    # 6. Batch/Lot > 20 characters must be rejected
    with pytest.raises(ValidationError):
        LCAInput(**base_kwargs, batch_lot="123456789012345678901")
