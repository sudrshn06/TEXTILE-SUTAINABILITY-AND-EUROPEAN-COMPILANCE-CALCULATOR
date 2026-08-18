# CHAKRA-AI Security Design

## Implemented secure-design principles

| Principle | CHAKRA implementation |
|---|---|
| Least privilege | Server-side RBAC: calculator, auditor and security-admin capabilities are separate. |
| Separation of duties | A submitting user cannot mint their own DPP; an independent Compliance Auditor from the same factory must review it. |
| Defense in depth | Argon2id authentication, HttpOnly cookies, CSRF, RBAC, validation, rate limits, account lockout, tenant isolation, origin checks, idle session expiry, browser hardening headers, signatures and audit logs. |
| Fail-safe defaults | API failure does not fall back to fake/offline trusted results; unauthenticated/unauthorized actions are denied. |
| Complete mediation | Every protected API call resolves the current server session and checks permission; browser role labels are never trusted. |
| Compartmentalization | Users are isolated by factory; privileged audit/revocation functions are separately authorized. |
| Economy of mechanism | One FastAPI security boundary, one local identity store and one signature system; no unnecessary blockchain/auth frameworks. |
| Open design | Security does not depend on hiding algorithms. Password hashes/private keys/session tokens are the secrets, not source-code obscurity. |
| Least common mechanism | Per-user sessions and factory isolation prevent one shared browser identity or shared role switch from authorizing everyone. |
| Psychological acceptability | Standard sign-in, clear review queue and simple approve/reject flow; security is visible without forcing users to handle cryptographic keys. |
| Secure weakest links | Browser CSV input is size-limited/sanitized; server request bodies are size-limited; hardcoded signing secret removed; private key is git-ignored. |
| Secure by default | Accounts are provisioned out-of-band, roles cannot be self-selected, sessions are HttpOnly/SameSite, APIs deny access by default. |

## Attack classes addressed

- Role/privilege spoofing from the old frontend login
- Direct unauthorized API calls / IDOR across factories
- CSRF on state-changing endpoints
- Session theft exposure from JavaScript (`HttpOnly` cookie)
- Reuse of a stolen browser session from a different user-agent (session binding)
- Long-idle browser sessions (idle timeout)
- Cross-origin state-changing browser requests (explicit Origin allowlist + CSRF)
- Password database compromise (Argon2id hashes)
- Brute-force login attempts (IP throttling + temporary account lockout)
- Replay/client tampering of DPP values (DPP derived from persisted server calculation)
- Passport forgery (Ed25519 signatures)
- Passport lifecycle compromise (revocation)
- Basic request-body DoS
- Clickjacking, MIME sniffing, referrer leakage and unnecessary browser capabilities
- DOM-XSS through uploaded CSV supplier names
- Fail-open behavior when the backend is unavailable
- Wildcard CORS
- Hardcoded passport signing secret

## Intentional limitations / remaining risks

This is a hardened hackathon/student application, not a formal security certification.

1. The original UI still uses inline JavaScript/styles and third-party CDN libraries, so CSP must currently allow `unsafe-inline` and those CDN origins. A production hardening pass should bundle/vend dependencies locally and remove inline event handlers.
2. SQLite is appropriate for a local/demo deployment. A multi-instance public deployment should use a managed database with encrypted backups, strong database IAM and migrations.
3. The in-memory IP rate limiter resets on restart and is not shared across replicas. Put an internet-facing deployment behind a reverse proxy/API gateway/WAF with distributed rate limiting.
4. TLS is not terminated by this development server. Production must use HTTPS and `CHAKRA_COOKIE_SECURE=1`.
5. The generated Ed25519 development key is stored as a local PEM file. Production should use a protected secret manager/KMS/HSM and key rotation.
6. Bulk-supplier/city visualization logic from the original prototype remains client-side demo analytics. Those views cannot authorize a passport or modify trusted server calculation records.
7. The security layer does not validate the scientific/regulatory correctness of the project's underlying sustainability constants or claims.

## Sensitive generated files

Do not commit/share:

- `yugam/data/chakra_security.db`
- `yugam/data/passport_ed25519.pem`
- `.env`

They are excluded by `.gitignore`.

## 2026-08-10 verification hardening

The current build also includes:

- Environment-configurable login/public/authenticated/web request limits.
- Per-account exponential login backoff (capped, temporary) instead of a fixed hard lockout threshold.
- Actual request-body length enforcement even when `Content-Length` is absent or misleading.
- JSON content-type enforcement for API requests with bodies.
- Strict Pydantic models that forbid unexpected fields and reject NaN/Infinity.
- Server-side cross-field upper-bound validation for process ratios, so browser checks cannot be bypassed with direct API calls.
- A generic unhandled-exception response with a request ID, while detailed exception information remains server-side.
- Shared CSV type/size/header/content pre-validation for both CSV workflows.
- Sanitized/allowlisted state names before CSV-derived values are inserted into HTML.
- Automated security regression tests under `tests/test_security.py`.

Run the regression suite with:

```powershell
python -m pytest -q
```

For a live dependency vulnerability lookup, install development tools and run:

```powershell
python -m pip install -r requirements-dev.txt
pip-audit -r requirements.txt
```

## Security verification report

See `SECURITY_VERIFICATION.md` for the executable regression-test results, fixes applied on 2026-08-10, and clean-environment dependency-audit steps.
