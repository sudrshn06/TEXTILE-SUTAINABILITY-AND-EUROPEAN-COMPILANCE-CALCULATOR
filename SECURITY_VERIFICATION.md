# CHAKRA-AI Security Verification Report

Date: 2026-08-10

## Scope

This review applied the project's security/production-readiness checklist to the current CHAKRA-AI source and verified controls in executable code rather than relying only on documentation.

## Already present and verified

The source already contained substantial security work:

- Argon2id password hashing.
- HttpOnly, SameSite session cookies.
- CSRF protection on state-changing authenticated APIs.
- Server-side role-based authorization.
- Factory/tenant isolation for protected data.
- Separation of duties for passport review/minting.
- Trusted origin/host checks and browser security headers.
- Request-body limit.
- Ed25519-signed Digital Product Passports with public verification and revocation.
- Audit logging.
- Parameterized SQLite operations.
- `.env`, database files and signing keys excluded by `.gitignore`.

## Gaps found and fixed

### 1. Login backoff persistence bug
The previous failed-login update happened inside a SQLite context and then raised an HTTP exception. The exception caused the transaction to roll back, so failed-attempt state was not reliably persisted.

Fix: commit failed-attempt/backoff state before returning the 401 response.

### 2. Configurable rate limiting
Rate-limit thresholds and windows are now configurable through environment variables instead of being hardcoded.

Added configuration:

- `CHAKRA_LOGIN_IP_LIMIT`
- `CHAKRA_LOGIN_IP_WINDOW`
- `CHAKRA_PUBLIC_API_LIMIT`
- `CHAKRA_PUBLIC_API_WINDOW`
- `CHAKRA_AUTH_API_LIMIT`
- `CHAKRA_AUTH_API_WINDOW`
- `CHAKRA_WEB_RATE_LIMIT`
- `CHAKRA_WEB_RATE_WINDOW`

429 responses now include `Retry-After`.

### 3. Exponential authentication backoff
Per-account failed logins now use bounded exponential backoff rather than a single fixed lockout period.

Configuration:

- `CHAKRA_AUTH_BACKOFF_BASE`
- `CHAKRA_AUTH_BACKOFF_MAX`

### 4. Strict API schemas
API request models now:

- reject unknown fields,
- reject NaN/infinity,
- strip surrounding string whitespace,
- preserve Pydantic type/range validation.

The LCA calculation endpoint also enforces server-side physical upper bounds so an attacker cannot bypass browser validation by calling the API directly.

### 5. Request body/content-type enforcement
For POST API requests the middleware now checks the actual body length in addition to `Content-Length` and rejects non-JSON bodies where JSON is required.

### 6. CSV upload hardening
Both CSV workflows now use a common pre-validation function checking:

- extension,
- MIME type where supplied by the browser,
- maximum file size,
- text/NUL sanity,
- required headers.

Large city-data files are capped to a bounded number of processed rows.

### 7. CSV-to-DOM injection path
CSV `State` values are normalized to an allowed state key before being displayed/used. This removes the raw state-name path that could flow into HTML rendering.

### 8. Generic server error handling
Unhandled exceptions return a generic error plus request ID rather than stack traces/internal details. The exception type is recorded server-side for debugging/audit purposes.

### 9. Dependency floors
`requirements.txt` was raised to modern patched framework/crypto releases:

- FastAPI `>=0.141.1,<1`
- Starlette `>=1.6.0,<2`
- cryptography `>=50.0.0,<51`

`requirements-dev.txt` includes `pip-audit` for repeatable dependency advisory scanning.

## Automated verification

Command:

```bash
PYTHONPATH=. pytest -q tests/test_security.py
```

Result at packaging time:

```text
.......                                                                  [100%]
7 passed
```

The suite verifies:

1. Browser security headers and no-store behavior.
2. Session cookie, CSRF, RBAC and cross-factory IDOR isolation.
3. Origin rejection, strict schema rejection, process-bound validation, content-type rejection and request-size rejection.
4. Exponential per-account login backoff persistence.
5. Independent review flow, signed DPP verification and DPP revocation.
6. Both frontend CSV paths use the shared validation and state allow-list logic.
7. Configurable rate limiting actually returns 429 and `Retry-After` when the configured threshold is exceeded.

Additional checks completed:

- Python syntax compilation: PASS.
- Inline JavaScript syntax (`node --check`): PASS.
- Hardcoded common secret/key pattern scan: no credential/key match found.
- `.env`, database and private-key paths are gitignored.

## Dependency-audit limitation

The execution environment used for this patch had older preinstalled dependencies and its package mirror did not provide the newest releases. Therefore the functional regression suite ran against the available runtime, while `requirements.txt` now requests the patched dependency floors above.

On a normal internet-connected development machine, run a clean install and repeat the tests:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements-dev.txt
pip-audit -r requirements.txt
PYTHONPATH=. pytest -q tests/test_security.py
```

Do not call the dependency review complete until `pip-audit` has been run against the actually installed deployment environment.

## Production notes

- Put the application behind HTTPS and set `CHAKRA_COOKIE_SECURE=1`.
- The in-memory rate limiter is suitable for a single-process hackathon/demo deployment. A multi-instance production deployment should use a shared limiter such as Redis or an API gateway.
- SQLite is suitable for the current demo/small deployment. Larger concurrent deployments should move persistent state to a production database.
- The current CSP must still permit inline code/CDNs used by the single-file frontend. A future production UI should self-host assets and use nonce/hash-based CSP instead of weakening script policy.
