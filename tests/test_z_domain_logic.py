from pathlib import Path

from yugam.app import (
    CEA_GRID_AVERAGE_KG_PER_KWH,
    LCAInput,
    _compute_lca,
)


def sample(**overrides):
    base = dict(
        state='Tamil Nadu', fiber=3, weight_kg=1000,
        spin_kwh=800, weave_kwh=700, wet_kwh=1500,
        water_liters=30000, chemicals_kg=80, sew_kwh=300,
        waste_kg=30, packaging_kg=15,
    )
    base.update(overrides)
    return LCAInput(**base)


def test_cea_v21_default_and_no_generic_water_carbon():
    r = _compute_lca(sample())['data']
    assert r['grid_factor'] == CEA_GRID_AVERAGE_KG_PER_KWH == 0.710
    assert r['factor_provenance']['water']['value'] == 0.0
    assert r['factor_provenance']['water']['quality'] == 'double_counting_avoided'


def test_stage_sum_matches_total():
    r = _compute_lca(sample())['data']
    s = r['stages']
    assert abs((s['material'] + s['spinning'] + s['weaving'] + s['dyeing'] + s['assembly']) - r['carbon_total_kg']) < 0.1
    assert abs(r['carbon_total_kg'] / 1000 - r['carbon_intensity']) < 0.01


def test_override_requires_source():
    try:
        sample(electricity_factor_override=0.2)
        assert False, 'expected validation error'
    except Exception:
        pass


def test_thermal_heat_separate_and_sourced():
    r0 = _compute_lca(sample())['data']
    r1 = _compute_lca(sample(wet_heat_kwh=1000, wet_heat_factor=0.25, wet_heat_factor_source='metered boiler factor'))['data']
    assert abs((r1['carbon_total_kg'] - r0['carbon_total_kg']) - 250.0) < 0.1
    assert r1['stages']['thermal_heat'] == 250.0


def test_buyer_benchmark_is_scenario_not_eu_law():
    r = _compute_lca(sample(buyer_benchmark_kgco2e_per_kg=20, buyer_benchmark_source='Buyer contract v1', carbon_price_inr_per_tonne=5000))['data']
    assert r['buyer_benchmark']['benchmark_kgco2e_per_kg'] == 20
    assert r['reference_benchmark'] == r['buyer_benchmark']
    assert r['buyer_benchmark']['module'] == 'CHAKRA Reference Benchmark'
    assert r['buyer_benchmark']['display_status'] == 'PASS'
    assert r['buyer_benchmark']['scenario_type'] == 'user_supplied_sourced_reference'
    assert 'not an official compliance target' in r['buyer_benchmark']['legal_status']


def test_benchmark_without_price_is_not_reported_as_zero_cost():
    r = _compute_lca(sample(
        buyer_benchmark_kgco2e_per_kg=1,
        buyer_benchmark_source='Buyer contract v1',
    ))['data']['buyer_benchmark']
    assert r['status'] == 'FAIL'
    assert r['exposure_calculated'] is False
    assert r['scenario_exposure_inr'] == 0.0


def test_high_impact_input_fails_sourced_benchmark_with_nonzero_exposure():
    r = _compute_lca(sample(
        fiber=8,
        spin_kwh=5000,
        weave_kwh=5000,
        wet_kwh=10000,
        water_liters=200000,
        chemicals_kg=400,
        sew_kwh=5000,
        waste_kg=300,
        packaging_kg=100,
        buyer_benchmark_kgco2e_per_kg=15.0,
        buyer_benchmark_source='Buyer contract sustainability schedule v1',
        carbon_price_inr_per_tonne=6300.0,
    ))['data']

    benchmark = r['buyer_benchmark']
    assert benchmark['benchmark_kgco2e_per_kg'] == 15.0
    assert benchmark['source'] == 'Buyer contract sustainability schedule v1'
    assert benchmark['scenario_carbon_price_inr_per_tonne'] == 6300.0
    assert benchmark['status'] == 'FAIL'
    assert benchmark['display_status'] == 'NEEDS ATTENTION'
    assert benchmark['position'] == 'ABOVE_REFERENCE'
    assert benchmark['pass'] is False
    assert benchmark['difference_kgco2e_per_kg'] > 0
    assert benchmark['scenario_excess_tco2e'] > 0
    assert benchmark['scenario_exposure_inr'] > 0
    assert r['is_eu_compliant'] is False
    assert r['eu_tax_exposure_inr'] == benchmark['scenario_exposure_inr']


def test_high_impact_input_does_not_invent_unsourced_benchmark_or_penalty():
    r = _compute_lca(sample(
        fiber=8,
        spin_kwh=5000,
        weave_kwh=5000,
        wet_kwh=10000,
        water_liters=200000,
        chemicals_kg=400,
        sew_kwh=5000,
        waste_kg=300,
        packaging_kg=100,
    ))['data']

    assert r['ai_decision']['risk_tier'] == 'HIGH'
    assert r['buyer_benchmark'] is None
    assert r['is_eu_compliant'] is None
    assert r['eu_tax_exposure_inr'] == 0.0


def test_user_reported_inputs_fail_expected_stages_and_generate_action_plan():
    r = _compute_lca(LCAInput(
        state='Tamil Nadu', fiber=3, weight_kg=5000,
        spin_kwh=11992, weave_kwh=1100, wet_kwh=24989,
        water_liters=300000, chemicals_kg=1500, sew_kwh=1000,
        waste_kg=743, packaging_kg=50,
    ))['data']

    assessment = r['operational_assessment']
    assert assessment['status'] == 'FAIL'
    assert assessment['failed_stage_count'] == 3
    assert set(assessment['failed_stages']) == {'Spinning', 'Dyeing & Washing', 'Cut & Sew'}
    assert all(item['emissions_kgco2e'] > 0 for item in assessment['violations'])
    assert all(item['action'] for item in assessment['violations'])
    assert assessment['action_plan'][0]['source'] == 'XGBoost-guided failed-stage priority'
    assert assessment['action_plan'][0]['stage'] in assessment['failed_stages']
    assert len(assessment['action_plan']) >= 3
    assert r['ai_decision']['model_risk_tier'] == 'LOW'
    assert r['ai_decision']['risk_tier'] == 'HIGH'
    assert r['ai_decision']['risk_guardrail_applied'] is True
    assert r['ai_decision']['risk_confidence_applies_to'] == 'model_risk_tier'
    assert r['buyer_benchmark'] is None
    assert r['eu_tax_exposure_inr'] == 0.0


def test_ui_keeps_operational_fail_action_plan_and_sourced_scenario_output():
    html = (Path(__file__).resolve().parents[1] / 'yugam' / 'index.html').read_text(encoding='utf-8')
    assert 'Sourced Financial Exposure' in html
    assert 'CHAKRA Reference Benchmark - Simulation' in html
    assert 'PASS - within CHAKRA reference' in html
    assert 'NEEDS ATTENTION - above CHAKRA reference' in html
    assert 'Add a sourced scenario to estimate exposure' in html
    assert 'CCTS Awareness Simulator' in html
    assert 'Illustrative CCTS Market Exposure' in html
    assert 'scenario_exposure_inr' in html
    assert 'CHAKRA Operational FAIL' in html
    assert 'Complete Corrective Action Sequence' in html
    assert 'renderBenchmarkVisual' in html
    assert 'buildBulkOperationalViolations' in html
    assert 'f.violations && f.violations.length > 0' in html
    assert 'id="wet_heat"' in html and 'id="wet_heat_factor"' in html
    assert 'id="ccts_target"' in html and 'id="ccts_target_source"' in html
    assert 'risk_guardrail_applied' in html
    assert 'Security Audit Log' in html
    assert 'parseFloat(row.Weight_kg)||1000' not in html
    assert 'regression proves' not in html.lower()
    assert 'not a live monitoring feed' in html
    assert 'not live vendor identities' in html
    assert "'Tiruppur Avg', 'Surat Avg', 'Ludhiana Avg', 'Panipat Avg'" not in html


def test_supply_chain_map_uses_fixed_positions_and_viewport_scaling_only():
    html = (Path(__file__).resolve().parents[1] / 'yugam' / 'index.html').read_text(encoding='utf-8')
    assert 'SCOPE3_NODE_POSITIONS' in html
    assert 'fixed:{x:true,y:true}' in html
    assert 'physics:false' in html
    assert 'dragNodes:false' in html
    assert 'dragView:false' in html
    assert 'zoomView:false' in html
    assert 'improvedLayout:false' in html
    assert 'fitScope3Diagram(false)' in html
    assert 'aspect-ratio:16 / 9' in html
    assert 'hierarchical: {' not in html


def test_animation_tracker_requirements_are_present_in_ui():
    html = (Path(__file__).resolve().parents[1] / 'yugam' / 'index.html').read_text(encoding='utf-8')
    required_markers = [
        'animateElementValue', 'motion-surface', 'fade-surface', 'spotlight-card',
        'animated-list-item', 'workflow-stepper', 'dpp-tilt-card', 'glare-hover',
        'threads-bg', 'data-view-motion', 'blur-title', 'revealMotionSurfaces',
        'renderFactoryFlow', 'renderBenchmarkVisual', 'renderEvidenceCheck',
        'renderXgbAnalysis', 'verifyAndRevealPassport',
    ]
    assert all(marker in html for marker in required_markers)
    assert '@media (prefers-reduced-motion: reduce)' in html


def test_ccts_requires_sourced_target_and_is_scenario():
    r = _compute_lca(sample(ccts_target_intensity=20, ccts_target_source='Applicable notified target reference', ccts_price_inr_per_tonne=1000))['data']
    assert r['ccts_scenario'] is not None
    assert r['ccts_scenario']['module'] == 'CCTS Awareness Simulator'
    assert r['ccts_scenario']['position'] == 'ILLUSTRATIVE_SURPLUS'
    assert 'Illustrative awareness scenario only' in r['ccts_scenario']['note']
    assert r['ccts_scenario']['value_calculated'] is True
    assert r['ccts_scenario']['scenario_value_inr'] > 0
    assert r['ccts_scenario']['scenario_exposure_inr'] == 0
    assert r['ccts_scenario']['actual_emissions_tco2e'] < r['ccts_scenario']['target_emissions_tco2e']
    assert r['ccts_scenario']['framework_source'].startswith('https://beeindia.gov.in/')
    assert 'obligated-entity status' in r['ccts_scenario']['note']
    assert r['is_ccts_eligible'] is False


def test_ccts_shortfall_reports_exposure_instead_of_zero_value():
    r = _compute_lca(sample(
        ccts_target_intensity=1,
        ccts_target_source='Illustrative planning target; not a notified target',
        ccts_price_inr_per_tonne=1250,
    ))['data']['ccts_scenario']
    assert r['position'] == 'ILLUSTRATIVE_SHORTFALL'
    assert r['scenario_shortfall_tco2e'] > 0
    assert r['illustrative_ccc_shortfall'] == r['scenario_shortfall_tco2e']
    assert r['scenario_exposure_inr'] == round(r['scenario_shortfall_tco2e'] * 1250, 2)
    assert r['scenario_value_inr'] == 0
    assert r['exposure_calculated'] is True


def test_ccts_without_price_keeps_financial_amounts_not_calculated():
    r = _compute_lca(sample(
        ccts_target_intensity=1,
        ccts_target_source='Illustrative planning target; not a notified target',
    ))['data']['ccts_scenario']
    assert r['scenario_shortfall_tco2e'] > 0
    assert r['scenario_exposure_inr'] == 0
    assert r['value_calculated'] is False
    assert r['exposure_calculated'] is False


def test_partial_scenarios_are_rejected_instead_of_silently_ignored():
    for values in [
        {'carbon_price_inr_per_tonne': 1000},
        {'buyer_benchmark_source': 'orphan source'},
        {'ccts_price_inr_per_tonne': 1000},
        {'ccts_target_source': 'orphan target source'},
    ]:
        try:
            sample(**values)
            assert False, f'expected validation error for {values}'
        except Exception:
            pass


def test_xgboost_not_authoritative():
    r = _compute_lca(sample())
    assert r['model']['authoritative'] is False
    assert r['model']['external_validation'] is False
    assert 'not real-world accuracy' in r['model']['note']


def test_dpp_readiness_not_certification():
    r = _compute_lca(sample())['data']
    assert r['espr_dpp_readiness']['status'] == 'technical_readiness_only'
    assert r['espr_dpp_readiness']['product_specific_espr_rules_verified'] is False
    assert 'not EU/government certification' in r['espr_dpp_readiness']['note']
