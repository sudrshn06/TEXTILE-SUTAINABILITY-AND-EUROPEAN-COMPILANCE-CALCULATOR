# CHAKRA-AI ML Validation Report

Training population: 12,000 real-world-data-calibrated hybrid textile examples.

## Internal holdout
- Sustainability risk tier accuracy: 97.38%
- Priority production stage accuracy: 93.63%

## 5-fold stratified cross-validation
- Sustainability risk tier: mean 97.04%, std 0.38 percentage points
- Priority production stage: mean 94.29%, std 0.34 percentage points

## Interpretation
These results show that the XGBoost decision engine consistently learns the decision labels within the hybrid reference population. They are not external factory-validation metrics. Independent pilot data from factories that were not used to construct/calibrate the training population is required before claiming real-world predictive accuracy.

See REAL_DATA_TRAINING.md for data provenance and claim wording.
