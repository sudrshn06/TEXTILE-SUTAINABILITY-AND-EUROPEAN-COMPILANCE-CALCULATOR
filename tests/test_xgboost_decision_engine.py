import math
from unittest.mock import patch

from yugam.app import LCAInput, _compute_lca, ML_RISK_LABELS, ML_STAGE_LABELS
from yugam.ml_explainability import FEATURE_DEFINITIONS, explain_stage_prediction


def _sample(**overrides):
    data = dict(
        state='Tamil Nadu', fiber=1, weight_kg=5000,
        spin_kwh=5000, weave_kwh=6000, wet_kwh=15000,
        water_liters=250000, chemicals_kg=500,
        sew_kwh=2000, waste_kg=300, packaging_kg=100,
    )
    data.update(overrides)
    return LCAInput(**data)


def test_xgboost_drives_operational_decision_output():
    result = _compute_lca(_sample())
    model = result['model']
    decision = result['data']['ai_decision']
    assert model['type'] == 'XGBoost decision-support engine'
    assert model['decision_authority'] == 'operational_priority_only'
    assert decision['engine'] == 'XGBoost decision-support engine'
    assert decision['risk_tier'] in ML_RISK_LABELS
    assert decision['priority_stage'] in ML_STAGE_LABELS
    assert decision['priority_action']
    assert len(decision['top_stage_ranking']) == 3
    assert 0 <= decision['risk_confidence'] <= 1
    assert decision['risk_confidence_applies_to'] == 'model_risk_tier'
    assert decision['model_risk_tier'] in ML_RISK_LABELS
    assert 0 <= decision['model_risk_confidence'] <= 1
    assert 0 <= decision['stage_confidence'] <= 1


def test_ml_does_not_replace_lca_or_regulatory_facts():
    result = _compute_lca(_sample())
    assert result['model']['authoritative'] is False
    assert result['data']['legal_note']
    assert result['data']['carbon_total_kg'] > 0
    assert result['data']['factor_provenance']
    assert result['data']['espr_dpp_readiness']['status'] == 'technical_readiness_only'


def test_shap_explainability_structure_and_direction():
    result = _compute_lca(_sample())
    decision = result['data']['ai_decision']
    assert 'ml_explanation' in decision
    explanation = decision['ml_explanation']
    assert explanation is not None
    assert explanation['method'] == 'TreeSHAP'
    assert explanation['target'] == decision['priority_stage']
    assert 1 <= len(explanation['features']) <= 5

    valid_keys = {f['key'] for f in FEATURE_DEFINITIONS}
    for item in explanation['features']:
        assert item['feature'] in valid_keys
        assert isinstance(item['feature_name'], str) and len(item['feature_name']) > 0
        assert math.isfinite(item['shap_value'])
        assert item['direction'] in ('toward', 'away')
        if item['shap_value'] >= 0:
            assert item['direction'] == 'toward'
        else:
            assert item['direction'] == 'away'
        assert 0 <= item['relative_influence_percent'] <= 100

    assert 'disclaimer' in explanation
    assert 'SHAP explains' in explanation['disclaimer']


def test_shap_feature_ordering_matches_model_input():
    assert len(FEATURE_DEFINITIONS) == 14
    expected_keys = [
        'grid_electricity_factor',
        'raw_fiber_factor',
        'spinning_electricity_intensity',
        'weaving_electricity_intensity',
        'wet_process_electricity_intensity',
        'wet_process_thermal_intensity',
        'wet_process_thermal_factor',
        'water_consumption_intensity',
        'chemical_consumption_ratio',
        'sewing_electricity_intensity',
        'fabric_waste_ratio',
        'packaging_mass_ratio',
        'regional_water_stress',
        'batch_carbon_intensity',
    ]
    actual_keys = [f['key'] for f in FEATURE_DEFINITIONS]
    assert actual_keys == expected_keys


def test_shap_targets_predicted_stage_class():
    sample_input = _sample(wet_kwh=40000, water_liters=600000, chemicals_kg=1200)
    result = _compute_lca(sample_input)
    decision = result['data']['ai_decision']
    explanation = decision.get('ml_explanation')
    assert explanation is not None
    assert explanation['target'] == decision['priority_stage']
    assert explanation['target_stage_index'] == ML_STAGE_LABELS.index(decision['priority_stage'])


def test_shap_failure_does_not_break_calculation():
    with patch('yugam.app.explain_stage_prediction', side_effect=RuntimeError('Simulated SHAP failure')):
        result = _compute_lca(_sample())
        decision = result['data']['ai_decision']
        assert decision['risk_tier'] in ML_RISK_LABELS
        assert decision['priority_stage'] in ML_STAGE_LABELS
        assert decision['ml_explanation'] is None
        assert result['data']['carbon_total_kg'] > 0
