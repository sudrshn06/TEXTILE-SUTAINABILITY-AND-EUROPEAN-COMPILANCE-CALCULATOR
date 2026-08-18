# Ethical and Logical Consistency Fixes — 2026-08-11

This correction preserves the calculator, bulk check, regional-data view, process map, XGBoost engine, action plan, security controls, auditor workflow and signed DPP. No important feature was removed.

## Corrected behavior

- A relative-cohort XGBoost `LOW` can no longer hide measured stage failures. The final CHAKRA decision tier is raised by a disclosed deterministic guardrail while the raw ML tier and confidence remain visible.
- Corrective action starts with an actually failed stage. XGBoost ranks among failed stages instead of placing a passing stage first.
- Missing buyer price, CCTS price or target data displays `Not calculated`; the app does not invent a fine, carbon price, notified target, eligibility decision or regulatory approval.
- Wet-process electricity and separately metered thermal heat are distinct inputs. Thermal heat requires a factor and source.
- Auditors see operational PASS/FAIL and failed stages before signing. Signed DPPs preserve that result and explicitly state that signing does not turn a FAIL into a PASS.
- Security Admin now has an in-app audit-log view while the existing server-side audit and revocation controls remain unchanged.
- Bulk CSV zero values are no longer silently replaced by defaults. Invalid rows are skipped and disclosed; bulk results remain pre-screening.
- The regional CSV view is labelled as descriptive and non-causal. Synthetic data is clearly labelled, and the process map uses neutral stage labels rather than invented supplier identities.
- Misleading efficiency, causation, live-monitoring, certification and statutory-fine language was replaced with scoped, evidence-aware wording.

## Verification

- 24 automated tests pass.
- Browser verification covered Production Manager calculation, high-impact FAIL, decision-tier guardrail, unsourced and sourced financial scenarios, separately metered heat, CCTS scenario, Auditor failure disclosure, signed-DPP failure preservation, Security Admin audit access, and the regional/map disclosures.
- The current Indian grid default remains CEA CO2 Baseline Database Version 21.0 (`0.710 kg CO2e/kWh`).
- CCTS remains a scenario because entity-specific notified targets, scheme monitoring and accredited verification are required for actual compliance or certificate issuance.
