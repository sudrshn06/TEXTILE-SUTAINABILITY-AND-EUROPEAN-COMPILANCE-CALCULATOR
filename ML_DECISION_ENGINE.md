# CHAKRA-AI XGBoost Decision Engine

This build keeps all existing CHAKRA-AI features and uses XGBoost only for transparent operational decision support.

## XGBoost now does
- Predicts a relative-cohort sustainability-risk tier: LOW / MEDIUM / HIGH.
- Predicts the highest-priority process stage: Raw Materials, Spinning, Weaving, Dyeing & Washing, or Cut & Sew.
- Returns confidence/probabilities and a top-3 stage ranking.
- Ranks failed stages for the first corrective action. A passing stage may never be placed ahead of a measured threshold failure.

## Decision-risk guardrail

The user-facing CHAKRA decision tier is the stricter of the raw XGBoost cohort tier and a deterministic operational floor. One failed stage sets at least MEDIUM; two failed stages or a 50% threshold exceedance set at least HIGH. The API retains the raw model tier, probability and confidence separately so a guardrail-raised HIGH tier is never falsely described as a high-confidence ML prediction.

## XGBoost does not do
- It does not calculate the carbon footprint.
- It does not decide ESPR/DPP legal readiness.
- It does not determine CCTS eligibility or issue credits.
- It does not override factor provenance or data-quality grading.

Those outputs remain deterministic and auditable.

## Validation status
The ML training set is now a real-world-data-calibrated hybrid dataset based on published factory measurements and open textile LCA references, with controlled augmentation. Holdout metrics are internal hybrid-dataset validation and must not be presented as independent external factory accuracy. See REAL_DATA_TRAINING.md.

## Regression status
24 tests pass, including regression coverage for the ML/operational guardrail, sourced scenarios, action-plan ordering, signed-DPP failure disclosure, factory isolation and security controls.
