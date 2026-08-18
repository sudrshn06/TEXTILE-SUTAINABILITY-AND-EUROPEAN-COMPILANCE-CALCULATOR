# CHAKRA-AI — Secure Design Build

This build keeps the original four CHAKRA-AI workspace areas and adds a server-enforced security layer. Model details remain in the implementation instead of being repeated throughout the interface.

## 1. Install

```powershell
python -m pip install -r requirements.txt
```

## 2. Create accounts

Roles are provisioned locally. They are **not** selected in the browser.

Create a person who can submit calculations:

```powershell
python manage_users.py add --email manager@demo.com --name "Production Manager" --factory "Demo Textiles" --role "Production Manager"
```

Create an independent auditor for the **same factory**:

```powershell
python manage_users.py add --email auditor@demo.com --name "Compliance Auditor" --factory "Demo Textiles" --role "Compliance Auditor"
```

Optional security administrator:

```powershell
python manage_users.py add --email security@demo.com --name "Security Admin" --factory "Demo Textiles" --role "Security Admin"
```

Passwords are prompted securely and stored with Argon2id hashes.

List accounts:

```powershell
python manage_users.py list
```

## 3. Run

```powershell
.\start_chakra.bat
```

or:

```powershell
python -m uvicorn yugam.app:app --host 127.0.0.1 --port 8001
```

Open `http://127.0.0.1:8001`.

Do **not** open `index.html` directly. The secure version must be served by FastAPI so authentication, CSRF, RBAC and same-origin cookies work.

## Secure workflow

1. Production Manager / Sustainability Officer signs in.
2. Server authenticates the account and assigns the server-stored role/factory.
3. Calculation requests require a valid HttpOnly session + CSRF token + permitted role.
4. Calculation is stored server-side and receives a `CAL-...` ID.
5. A calculation with no unresolved data-integrity anomaly is submitted for independent review.
6. A different Compliance Auditor for the same factory sees its carbon result, operational PASS/FAIL and failed stages in the review queue.
7. Auditor approves or rejects it.
8. Approval creates an Ed25519-signed `DPP-...` ESPR/DPP-readiness record from **server-stored calculation data**. The signed record preserves operational failures; signing attests review and integrity, not sustainability performance or EU/government certification.
9. The QR points to the public verification endpoint. A Security Admin can revoke a compromised/invalid passport.

## Important environment settings

Copy `.env.example` values into your deployment environment as needed. For real deployment:

- put the app behind HTTPS;
- set `CHAKRA_COOKIE_SECURE=1`;
- store the SQLite DB and Ed25519 private key outside the source tree;
- restrict `CHAKRA_ALLOWED_ORIGINS` and `CHAKRA_ALLOWED_HOSTS` to the real domain;
- set an appropriate `CHAKRA_SESSION_IDLE` value for the deployment;
- use a reverse proxy/WAF for internet-facing rate limiting and TLS.

The app is hardened substantially, but no application can be guaranteed immune to every cyberattack. See `SECURITY.md` for implemented controls and remaining deployment risks.

## Security regression verification

After installing dependencies, run:

```powershell
python -m pytest -q
python -m pip check
```

For a current vulnerability-database audit (requires internet access):

```powershell
python -m pip install -r requirements-dev.txt
pip-audit -r requirements.txt
```

Rate limits and login backoff are configurable in `.env.example`; do not hardcode production thresholds in source.

### Security verification

See `SECURITY_VERIFICATION.md`. Run `PYTHONPATH=. pytest -q tests/test_security.py` after installation and `pip-audit -r requirements.txt` before deployment.


## Evidence-aware sustainability logic

See `DOMAIN_VERIFICATION.md` for the corrected CEA v21 grid factor, transparent CHAKRA KPI, source-traceable factor overrides, CCTS scenario logic, and ESPR/DPP-readiness framing.

The UI distinguishes wet-process electricity from optional separately metered thermal heat. Buyer exposure and CCTS value remain `Not calculated` unless their required sourced inputs are provided. The regional CSV view is descriptive demo analytics, not live monitoring or causal attribution, and the stage map is not presented as verified supplier traceability.
