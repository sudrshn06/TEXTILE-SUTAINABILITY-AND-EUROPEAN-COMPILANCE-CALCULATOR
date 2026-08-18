# CHAKRA-AI Domain Logic Verification — 12 Aug 2026

## What changed
- CEA CO2 Baseline Database v21.0 is the default electricity evidence anchor.
- FY 2024-25 weighted-average Indian Grid factor: 0.710 kgCO2/kWh.
- State-specific electricity factors were removed from the authoritative calculation because CEA v21 treats India as a unified grid; facility-specific verified overrides are supported with source provenance.
- Water volume is reported as resource use and is not automatically converted to CO2, avoiding the previous wet-process heat/water double count.
- Wet-process electricity and optional thermal heat are calculated separately. Thermal heat requires a factor and source.
- Factor overrides require provenance and produce an evidence/data-quality grade.
- CHAKRA Score is a transparent internal KPI with disclosed weights; it is not an official score.
- XGBoost is diagnostic only. Synthetic holdout R2 is explicitly not real-world accuracy and cannot change review/passport decisions.
- "Greenwashing detection" was replaced by rule-based data-integrity/plausibility checks; the system does not infer intent.
- No EU/CBAM textile threshold or statutory EU tax calculation is claimed. The CHAKRA Reference Benchmark is optional and must include its source; monetary exposure is not calculated unless a scenario price is explicitly supplied.
- Operational stage failures use the documented internal CHAKRA thresholds and remain separate from legal compliance, buyer contracts and monetary exposure.
- The CCTS Awareness Simulator requires a sourced facility target or clearly labelled planning target. It reports illustrative surplus/shortfall CCC-equivalents and optional market value/exposure, while making no claim of obligated-entity status, official compliance, eligibility, issuance, surrender obligation, or guaranteed revenue/cost.
- Signed DPP output is an independently reviewed CHAKRA ESPR/DPP-readiness record, not EU/government certification.

## Official anchors
- Central Electricity Authority: CO2 Baseline Database Version 21.0 / User Guide Version 21.0 (FY 2024-25).
- European Commission: Ecodesign for Sustainable Products Regulation (ESPR) and textile strategy; product-specific requirements and Digital Product Passport rollout are progressive.
- Bureau of Energy Efficiency: Carbon Credit Trading Scheme; obligated entities comply with prescribed/notified GHG-intensity targets, and below-target performance is subject to scheme verification/issuance.

## Verification
`python -m pytest -q`

Expected for this package: **28 passed**.

Tests cover security controls plus CEA v21 default-factor use, stage-sum arithmetic, source-required overrides, separate thermal heat, reference-benchmark PASS/NEEDS ATTENTION behavior, sourced CCTS surplus and shortfall scenarios, optional market pricing, stable map structure, all 17 visual markers, reduced motion, non-authoritative XGBoost, and non-certification DPP framing.
