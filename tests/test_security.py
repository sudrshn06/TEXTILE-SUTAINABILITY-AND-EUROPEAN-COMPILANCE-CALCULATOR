from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = Path(tempfile.mkdtemp(prefix="chakra-security-tests-"))
os.environ["CHAKRA_DB_PATH"] = str(TEST_DIR / "test.db")
os.environ["CHAKRA_SIGNING_KEY_PATH"] = str(TEST_DIR / "key.pem")
os.environ["CHAKRA_ALLOWED_ORIGINS"] = "http://testserver"
os.environ["CHAKRA_ALLOWED_HOSTS"] = "testserver"
os.environ["CHAKRA_LOGIN_IP_LIMIT"] = "100"
os.environ["CHAKRA_AUTH_API_LIMIT"] = "500"
os.environ["CHAKRA_PUBLIC_API_LIMIT"] = "500"
os.environ["CHAKRA_WEB_RATE_LIMIT"] = "500"
os.environ["CHAKRA_AUTH_BACKOFF_BASE"] = "1"
os.environ["CHAKRA_AUTH_BACKOFF_MAX"] = "16"

from yugam import app as mod  # noqa: E402


def teardown_module():
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def _add_user(email, name, factory, role, password="StrongPass123!"):
    with mod._db() as conn:
        conn.execute(
            "INSERT INTO users(email,name,factory,role,password_hash,active,created_at) VALUES(?,?,?,?,?,1,?)",
            (email, name, factory, role, mod.PASSWORD_HASHER.hash(password), int(time.time())),
        )


def _reset_state():
    mod._rate_store.clear()
    with mod._db() as conn:
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM passports")
        conn.execute("DELETE FROM calculations")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
    _add_user("manager@a.com", "Manager A", "Factory A", "Production Manager")
    _add_user("auditor@a.com", "Auditor A", "Factory A", "Compliance Auditor")
    _add_user("manager@b.com", "Manager B", "Factory B", "Production Manager")
    _add_user("security@a.com", "Security A", "Factory A", "Security Admin")


def setup_function():
    _reset_state()


def _login(client, email, password="StrongPass123!", ua="pytest-agent"):
    return client.post(
        "/api/v2/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://testserver", "User-Agent": ua},
    )


def _valid_payload():
    return {
        "state": "Tamil Nadu",
        "fiber": 3,
        "weight_kg": 1000,
        "spin_kwh": 500,
        "weave_kwh": 500,
        "wet_kwh": 1500,
        "water_liters": 10000,
        "chemicals_kg": 50,
        "sew_kwh": 100,
        "waste_kg": 20,
        "packaging_kg": 10,
    }


def test_security_headers_and_no_store():
    client = TestClient(mod.app, base_url="http://testserver")
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_auth_cookie_csrf_rbac_and_idor():
    manager = TestClient(mod.app, base_url="http://testserver")
    r = _login(manager, "manager@a.com", ua="manager-a")
    assert r.status_code == 200
    assert "chakra_session" in manager.cookies
    assert "HttpOnly" in r.headers.get("set-cookie", "")
    csrf = r.json()["csrf_token"]

    r = manager.post(
        "/api/v2/calculate", json=_valid_payload(),
        headers={"Origin": "http://testserver", "User-Agent": "manager-a"},
    )
    assert r.status_code == 403

    r = manager.post(
        "/api/v2/calculate", json=_valid_payload(),
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    calc_id = r.json()["calculation_id"]

    auditor = TestClient(mod.app, base_url="http://testserver")
    ar = _login(auditor, "auditor@a.com", ua="auditor-a")
    assert ar.status_code == 200
    acsrf = ar.json()["csrf_token"]
    r = auditor.post(
        "/api/v2/calculate", json=_valid_payload(),
        headers={"Origin": "http://testserver", "User-Agent": "auditor-a", "X-CSRF-Token": acsrf},
    )
    assert r.status_code == 403

    other = TestClient(mod.app, base_url="http://testserver")
    br = _login(other, "manager@b.com", ua="manager-b")
    assert br.status_code == 200
    r = other.get(f"/api/v2/my/calculations/{calc_id}", headers={"User-Agent": "manager-b"})
    assert r.status_code == 404


def test_saved_batch_without_operational_output_is_upgraded_on_read():
    client = TestClient(mod.app, base_url="http://testserver")
    login = _login(client, "manager@a.com", ua="manager-a")
    csrf = login.json()["csrf_token"]
    payload = _valid_payload() | {
        "weight_kg": 5000,
        "spin_kwh": 11992,
        "weave_kwh": 1100,
        "wet_kwh": 24989,
        "water_liters": 300000,
        "chemicals_kg": 1500,
        "sew_kwh": 1000,
        "waste_kg": 743,
        "packaging_kg": 50,
    }
    created = client.post(
        "/api/v2/calculate", json=payload,
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": csrf},
    )
    assert created.status_code == 200
    calc_id = created.json()["calculation_id"]

    with mod._db() as conn:
        row = conn.execute("SELECT result_json FROM calculations WHERE calculation_id=?", (calc_id,)).fetchone()
        stored = json.loads(row["result_json"])
        stored["data"].pop("operational_assessment")
        conn.execute("UPDATE calculations SET result_json=? WHERE calculation_id=?", (json.dumps(stored), calc_id))

    detail = client.get(f"/api/v2/my/calculations/{calc_id}", headers={"User-Agent": "manager-a"})
    assert detail.status_code == 200
    assessment = detail.json()["result"]["data"]["operational_assessment"]
    assert assessment["status"] == "FAIL"
    assert set(assessment["failed_stages"]) == {"Spinning", "Dyeing & Washing", "Cut & Sew"}


def test_origin_request_size_content_type_and_strict_schema():
    client = TestClient(mod.app, base_url="http://testserver")
    lr = _login(client, "manager@a.com", ua="manager-a")
    csrf = lr.json()["csrf_token"]

    r = client.post(
        "/api/v2/calculate", json=_valid_payload(),
        headers={"Origin": "https://evil.invalid", "User-Agent": "manager-a", "X-CSRF-Token": csrf},
    )
    assert r.status_code == 403

    payload = _valid_payload() | {"unexpected": "nope"}
    r = client.post(
        "/api/v2/calculate", json=payload,
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": csrf},
    )
    assert r.status_code == 422

    payload = _valid_payload()
    payload["spin_kwh"] = payload["weight_kg"] * 16
    r = client.post(
        "/api/v2/calculate", json=payload,
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": csrf},
    )
    assert r.status_code == 422

    r = client.post(
        "/api/v2/calculate", content="{}",
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": csrf, "Content-Type": "text/plain"},
    )
    assert r.status_code == 415

    huge = b"x" * (mod.MAX_BODY_BYTES + 1)
    r = client.post(
        "/api/v2/calculate", content=huge,
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_exponential_account_backoff():
    client = TestClient(mod.app, base_url="http://testserver")
    r = _login(client, "manager@a.com", password="wrong-one", ua="backoff")
    assert r.status_code == 401
    with mod._db() as conn:
        first = conn.execute("SELECT failed_attempts,locked_until FROM users WHERE email='manager@a.com'").fetchone()
        first_delay = int(first["locked_until"]) - int(time.time())
        conn.execute("UPDATE users SET locked_until=0 WHERE email='manager@a.com'")
    r = _login(client, "manager@a.com", password="wrong-two", ua="backoff")
    assert r.status_code == 401
    with mod._db() as conn:
        second = conn.execute("SELECT failed_attempts,locked_until FROM users WHERE email='manager@a.com'").fetchone()
        second_delay = int(second["locked_until"]) - int(time.time())
    assert second["failed_attempts"] == 2
    assert second_delay >= first_delay


def test_independent_review_signed_passport_and_revocation():
    manager = TestClient(mod.app, base_url="http://testserver")
    mr = _login(manager, "manager@a.com", ua="manager-a")
    mcsrf = mr.json()["csrf_token"]
    calc = manager.post(
        "/api/v2/calculate", json=_valid_payload(),
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": mcsrf},
    ).json()["calculation_id"]
    r = manager.post(
        f"/api/v2/calculations/{calc}/submit-review",
        headers={"Origin": "http://testserver", "User-Agent": "manager-a", "X-CSRF-Token": mcsrf},
    )
    assert r.status_code == 200

    auditor = TestClient(mod.app, base_url="http://testserver")
    ar = _login(auditor, "auditor@a.com", ua="auditor-a")
    acsrf = ar.json()["csrf_token"]
    queue = auditor.get("/api/v2/auditor/queue", headers={"User-Agent": "auditor-a"})
    queue_item = next(x for x in queue.json()["items"] if x["calculation_id"] == calc)
    assert queue_item["operational_status"] in {"PASS", "FAIL"}
    assert isinstance(queue_item["failed_stages"], list)
    minted = auditor.post(
        "/api/v2/mint-passport", json={"calculation_id": calc},
        headers={"Origin": "http://testserver", "User-Agent": "auditor-a", "X-CSRF-Token": acsrf},
    )
    assert minted.status_code == 200
    passport = minted.json()["passport"]
    pid = passport["passport_id"]
    assert passport["operational_status"] == queue_item["operational_status"]
    assert passport["failed_stages"] == queue_item["failed_stages"]
    assert "does not convert an operational FAIL" in passport["claim"]

    public = TestClient(mod.app, base_url="http://testserver")
    vr = public.get(f"/api/v2/passports/{pid}/verify")
    assert vr.status_code == 200
    assert vr.json()["signature_valid"] is True
    assert vr.json()["valid"] is True
    assert vr.json()["passport"]["operational_status"] == queue_item["operational_status"]

    admin = TestClient(mod.app, base_url="http://testserver")
    sr = _login(admin, "security@a.com", ua="security-a")
    scsrf = sr.json()["csrf_token"]
    rr = admin.post(
        f"/api/v2/passports/{pid}/revoke", json={"reason": "Test revocation"},
        headers={"Origin": "http://testserver", "User-Agent": "security-a", "X-CSRF-Token": scsrf},
    )
    assert rr.status_code == 200
    vr2 = public.get(f"/api/v2/passports/{pid}/verify")
    assert vr2.json()["signature_valid"] is True
    assert vr2.json()["valid"] is False
    assert vr2.json()["revoked"] is True


def test_frontend_csv_paths_use_common_validation_and_sanitize_state():
    html = (ROOT / "yugam" / "index.html").read_text(encoding="utf-8")
    assert "async function validateCsvFile" in html
    assert "async function handleCSVUpload" in html
    assert "async function handleSuratUpload" in html
    assert html.count("await validateCsvFile(file, required)") >= 2
    assert "Object.prototype.hasOwnProperty.call(STATE_DATA, requestedState)" in html


def test_configurable_rate_limit_enforced():
    client = TestClient(mod.app, base_url="http://testserver")
    old_limit, old_window = mod.WEB_RATE_LIMIT, mod.WEB_RATE_WINDOW
    try:
        mod.WEB_RATE_LIMIT = 2
        mod.WEB_RATE_WINDOW = 60
        mod._rate_store.clear()
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200
        r = client.get("/health")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
    finally:
        mod.WEB_RATE_LIMIT, mod.WEB_RATE_WINDOW = old_limit, old_window
        mod._rate_store.clear()
