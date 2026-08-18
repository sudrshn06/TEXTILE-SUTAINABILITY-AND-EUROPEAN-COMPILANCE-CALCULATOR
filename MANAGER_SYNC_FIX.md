# Manager approval sync fix

This build fixes the post-audit workflow.

- Submitters now have a server-backed `My Batches` list.
- Approval/rejection state persists across logout/login and refresh.
- On manager/sustainability-officer login, the latest submitted batch is restored from the server.
- Approved batches expose the auditor-issued Ed25519-signed DPP/QR to the original submitter.
- Rejected batches show the auditor's rejection reason.
- Manager history endpoints are owner- and factory-scoped and remain unavailable to auditors.

If upgrading an existing local install, replace only `yugam/app.py` and `yugam/index.html` so the existing `yugam/data/chakra_security.db` and accounts are preserved.
