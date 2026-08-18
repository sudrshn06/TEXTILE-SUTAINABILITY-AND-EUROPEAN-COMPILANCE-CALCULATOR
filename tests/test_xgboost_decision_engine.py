from yugam.app import LCAInput, _compute_lca, ML_RISK_LABELS, ML_STAGE_LABELS


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
