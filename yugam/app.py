"""
CHAKRA-AI Backend v2.3 — Evidence-Aware Textile Sustainability Screening
=========================================================================
This build separates three things that were previously mixed together:
  1. transparent screening-LCA arithmetic,
  2. an internal CHAKRA sustainability KPI, and
  3. legal / scheme readiness indicators.

Authoritative LCA/regulatory calculations are deterministic and source-traceable.
XGBoost is the operational decision-support brain: it interprets factory/process
data, predicts sustainability risk, identifies the priority production stage,
and drives corrective-action prioritization. Its synthetic holdout metrics are
never presented as real-world textile sustainability accuracy.

Current official anchors used by this build:
  - CEA CO2 Baseline Database v21.0 (FY 2024-25 weighted-average Indian Grid:
    0.710 tCO2/MWh = 0.710 kgCO2/kWh).
  - EU ESPR: product-specific requirements and Digital Product Passports are
    rolled out progressively; CHAKRA does NOT issue EU/government certification.
  - India CCTS: compliance depends on the notified GHG-intensity target for the
    obligated entity. CHAKRA therefore accepts a sourced target as scenario input
    instead of inventing a universal textile baseline.

The output is a screening / decision-support record, not an ISO-verified LCA,
EU customs clearance, CCTS certificate, or government approval.
"""

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, field_validator, model_validator, Field, ConfigDict
from collections import defaultdict
from pathlib import Path
from typing import Optional
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
import numpy as np

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

# XGBoost is the decision-support intelligence layer. Regulatory/LCA facts remain deterministic.
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

try:
    from yugam.ml_explainability import explain_stage_prediction
except ImportError:
    try:
        from ml_explainability import explain_stage_prediction
    except ImportError:
        explain_stage_prediction = None

try:
    from yugam.dpp_standards import get_standards_mapping_payload
except ImportError:
    try:
        from dpp_standards import get_standards_mapping_payload
    except ImportError:
        get_standards_mapping_payload = None

app = FastAPI(title="CHAKRA-AI Secure API", version="2.3.0", docs_url=None, redoc_url=None)

# ═══════════════════════════════════════════════════════════════════════════════
#  EVIDENCE-AWARE CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

CEA_GRID_VERSION = "21.0"
CEA_GRID_FY = "2024-25"
CEA_GRID_AVERAGE_KG_PER_KWH = 0.710
CEA_GRID_SOURCE = "Central Electricity Authority, CO2 Baseline Database v21.0, FY 2024-25 weighted-average Indian Grid"

# Location is retained for water-risk screening only. These are comparative
# prototype multipliers, not legal thresholds and not used as electricity factors.
STATE_WATER_STRESS = {
    "Andhra Pradesh": 3.8, "Arunachal Pradesh": 1.2, "Assam": 1.5,
    "Bihar": 3.0, "Chhattisgarh": 2.5, "Goa": 2.0, "Gujarat": 4.5,
    "Haryana": 4.8, "Himachal Pradesh": 1.5, "Jharkhand": 3.2,
    "Karnataka": 3.5, "Kerala": 1.8, "Madhya Pradesh": 3.6,
    "Maharashtra": 4.2, "Manipur": 1.4, "Meghalaya": 1.3,
    "Mizoram": 1.2, "Nagaland": 1.3, "Odisha": 2.8, "Punjab": 4.9,
    "Rajasthan": 5.0, "Sikkim": 1.1, "Tamil Nadu": 4.4,
    "Telangana": 3.9, "Tripura": 1.6, "Uttar Pradesh": 3.7,
    "Uttarakhand": 2.1, "West Bengal": 2.4, "Delhi": 4.5,
    "Chandigarh": 3.0, "Puducherry": 3.5, "Jammu and Kashmir": 2.0,
    "Ladakh": 3.0, "Andaman and Nicobar": 1.5, "Lakshadweep": 2.0,
    "Dadra and Nagar Haveli": 3.0, "Daman and Diu": 3.0,
}
STATE_ECOLOGY = {k: {"grid": CEA_GRID_AVERAGE_KG_PER_KWH, "water_stress": v} for k, v in STATE_WATER_STRESS.items()}

# Screening reference factors. They are deliberately identified as references,
# and users may replace them with supplier/EPD/LCA-specific factors + provenance.
FIBER_FACTORS = {
    1:  {"name": "Virgin Cotton",             "co2": 6.5},
    2:  {"name": "Organic Cotton",            "co2": 3.8},
    3:  {"name": "Recycled Cotton (rCot)",    "co2": 2.0},
    4:  {"name": "Virgin Polyester",          "co2": 9.5},
    5:  {"name": "Recycled Polyester (rPET)", "co2": 3.0},
    6:  {"name": "Nylon (Virgin)",            "co2": 14.0},
    7:  {"name": "Viscose / Rayon",           "co2": 4.0},
    8:  {"name": "Silk",                      "co2": 15.5},
    9:  {"name": "Jute",                      "co2": 1.2},
    10: {"name": "Hemp",                      "co2": 1.5},
}
FIBER_REFERENCE_SOURCE = "CHAKRA screening reference library; replace with verified supplier EPD/LCA factor for higher data quality"
DEFAULT_CHEMICAL_CO2_PER_KG = 3.0
DEFAULT_WASTE_CO2_PER_KG = 2.0
DEFAULT_PACKAGING_CO2_PER_KG = 2.8
SECONDARY_REFERENCE_SOURCE = "CHAKRA screening reference factor; replace with process/material-specific verified factor"

# Water volume is a resource-use indicator. No generic water->CO2 conversion is
# applied, preventing the previous heating/water double-counting problem.
WATER_CO2_PER_LITRE = 0.0

# Internal anomaly thresholds. They identify implausible / incomplete data only;
# they do not infer user intent and are not called 'greenwashing detection'.
MIN_WATER_RATIO = 5.0
MIN_CHEM_RATIO = 0.02
MIN_SPIN_RATIO = 0.20
MIN_WEAVE_RATIO = 0.20

# Existing CHAKRA operational screening thresholds. These drive internal stage
# PASS/FAIL results and action planning only; they are not legal limits or fines.
OPERATIONAL_THRESHOLDS = {
    "spinning_kwh_per_kg": 1.2,
    "weaving_kwh_per_kg": 1.0,
    "wet_process_kwh_per_kg": 4.0,
    "fabric_waste_kg_per_kg": 0.10,
}

# CHAKRA reference benchmarks and carbon prices must come from a user-supplied,
# sourced scenario. There is no universal EU/CBAM textile threshold or export
# penalty that can be defensibly hardcoded for every textile batch.
DEFAULT_BUYER_BENCHMARK = None
DEFAULT_CARBON_PRICE_INR_PER_TONNE = 0.0

CCTS_FRAMEWORK_SOURCE = "https://beeindia.gov.in/show_content.php?lang=1&level=1&lid=294&ls_id=116"
CCTS_COMPLIANCE_PROCEDURE_SOURCE = "https://beeindia.gov.in/sites/default/files/2024-07/Detailed%20Procedure%20for%20Compliance%20Procedure%20under%20CCTS.pdf"

PASSPORT_SECRET = None  # Ed25519 keypair is used below


def _chakra_score(carbon_intensity: float, kwh_per_kg: float, water_per_kg: float,
                  chem_ratio: float, waste_ratio: float, water_stress: float) -> tuple[float, dict]:
    """Transparent internal KPI. It is deliberately not a legal/official score."""
    carbon_score = 100.0 / (1.0 + max(carbon_intensity, 0.0) / 10.0)
    water_score = 100.0 / (1.0 + max(water_per_kg, 0.0) * max(water_stress, 1.0) / 100.0)
    energy_score = 100.0 / (1.0 + max(kwh_per_kg, 0.0) / 10.0)
    chemical_score = 100.0 / (1.0 + max(chem_ratio, 0.0) / 0.10)
    waste_score = 100.0 * max(0.0, 1.0 - min(max(waste_ratio, 0.0) / 0.20, 1.0))
    subscores = {
        "carbon": round(carbon_score, 1), "water": round(water_score, 1),
        "energy": round(energy_score, 1), "chemicals": round(chemical_score, 1),
        "waste": round(waste_score, 1),
    }
    weighted = (0.40*carbon_score + 0.25*water_score + 0.20*energy_score +
                0.10*chemical_score + 0.05*waste_score)
    return round(float(np.clip(weighted, 0.0, 100.0)), 1), subscores


def _reference_benchmark_scenario(actual_intensity: float, production_kg: float,
                                  reference_intensity: float, source: str,
                                  price_inr_per_tonne: Optional[float]) -> dict:
    """Compare a batch with a user-supplied, sourced CHAKRA reference.

    This deliberately does not choose a universal textile benchmark. The
    comparison is useful for awareness and buyer/planning scenarios only.
    """
    difference = actual_intensity - reference_intensity
    passed = difference <= 0
    excess_tco2e = max(0.0, difference * production_kg / 1000.0)
    price = price_inr_per_tonne if price_inr_per_tonne is not None else 0.0
    return {
        "module": "CHAKRA Reference Benchmark",
        "scenario_type": "user_supplied_sourced_reference",
        "actual_kgco2e_per_kg": round(actual_intensity, 3),
        "benchmark_kgco2e_per_kg": reference_intensity,
        "source": source,
        "status": "PASS" if passed else "FAIL",  # backward-compatible machine status
        "display_status": "PASS" if passed else "NEEDS ATTENTION",
        "position": "WITHIN_REFERENCE" if passed else "ABOVE_REFERENCE",
        "pass": passed,
        "difference_kgco2e_per_kg": round(difference, 3),
        "scenario_excess_tco2e": round(excess_tco2e, 4),
        "scenario_carbon_price_inr_per_tonne": price,
        "scenario_exposure_inr": round(excess_tco2e * price, 2),
        "exposure_calculated": price_inr_per_tonne is not None,
        "legal_status": "CHAKRA awareness/planning comparison only; not an official compliance target, EU statutory threshold, customs determination or guaranteed financial outcome.",
    }


def _ccts_awareness_scenario(actual_intensity: float, production_kg: float,
                             target_intensity: float, target_source: str,
                             price_inr_per_tonne: Optional[float]) -> dict:
    """Build an illustrative CCTS intensity scenario from sourced user inputs.

    BEE's compliance procedure uses the achieved-versus-target intensity
    difference multiplied by production. CHAKRA mirrors that arithmetic for
    awareness while making no eligibility, obligation, issuance or price claim.
    """
    difference_to_target = target_intensity - actual_intensity
    surplus_tco2e = max(0.0, difference_to_target * production_kg / 1000.0)
    shortfall_tco2e = max(0.0, -difference_to_target * production_kg / 1000.0)
    price = price_inr_per_tonne if price_inr_per_tonne is not None else 0.0
    if surplus_tco2e > 0:
        position = "ILLUSTRATIVE_SURPLUS"
    elif shortfall_tco2e > 0:
        position = "ILLUSTRATIVE_SHORTFALL"
    else:
        position = "AT_SCENARIO_TARGET"
    return {
        "module": "CCTS Awareness Simulator",
        "scenario_type": "illustrative_user_supplied_target",
        "position": position,
        "actual_intensity": round(actual_intensity, 3),
        "target_intensity": target_intensity,
        "target_source": target_source,
        "actual_emissions_tco2e": round(actual_intensity * production_kg / 1000.0, 4),
        "target_emissions_tco2e": round(target_intensity * production_kg / 1000.0, 4),
        "difference_to_target": round(difference_to_target, 3),
        "scenario_surplus_tco2e": round(surplus_tco2e, 4),
        "scenario_shortfall_tco2e": round(shortfall_tco2e, 4),
        "illustrative_ccc_surplus": round(surplus_tco2e, 4),
        "illustrative_ccc_shortfall": round(shortfall_tco2e, 4),
        "scenario_price_inr_per_tonne": price,
        "scenario_value_inr": round(surplus_tco2e * price, 2),
        "scenario_exposure_inr": round(shortfall_tco2e * price, 2),
        "value_calculated": price_inr_per_tonne is not None,
        "exposure_calculated": price_inr_per_tonne is not None,
        "framework_source": CCTS_FRAMEWORK_SOURCE,
        "calculation_method_source": CCTS_COMPLIANCE_PROCEDURE_SOURCE,
        "note": "Illustrative awareness scenario only. It does not establish obligated-entity status, official compliance, CCC eligibility or issuance, a surrender obligation, or guaranteed revenue/cost. Actual outcomes require the applicable notified entity target, monitoring, accredited verification and the CCTS process.",
    }


# ── XGBoost decision-support intelligence engine ───────────────────────────────
# The model interprets factory/process data and makes operational decisions:
#   1) sustainability risk tier, and
#   2) highest-priority manufacturing stage for corrective action.
# It does NOT replace source-traceable LCA arithmetic or legal/scheme checks.
ML_STAGE_LABELS = ["Raw Materials", "Spinning", "Weaving", "Dyeing & Washing", "Cut & Sew"]
ML_RISK_LABELS = ["LOW", "MEDIUM", "HIGH"]


# Public real-world calibration anchors used by the ML training generator.
# These do NOT replace the deterministic LCA factors used for reported footprints.
# Sources are documented in REAL_DATA_TRAINING.md.
PALAMUTCU_SEC = {
    "spin": (3.24, 3.47),       # kWh/kg, actual cotton spinning plant SEC
    "weave": (1.58, 2.24),     # kWh/kg, actual weaving plant SEC
    "wet_elec": (0.79, 1.05),  # kWh/kg, actual wet-processing electricity SEC
    "sew": (0.065, 0.195),     # kWh/kg, actual clothing-manufacturing SEC
}
PLOS_WET_PROCESS = {
    "water_mean": 136.0, "water_sd": 70.6, "water_min": 28.0, "water_max": 285.0,  # L/kg
    "chem_mean": 0.449, "chem_min": 0.152, "chem_max": 0.705,  # kg chemicals/kg textile
}
# Carbonfact 1.1.0 publicly reported process GHG reference points (kgCO2e/kg output)
# from the open process tables. Used as empirical anchors for stage-burden distributions.
CARBONFACT_WEAVING_GHG = np.array([0.76, 5.90, 1.49, 9.10, 7.59, 6.89, 6.10, 4.62, 4.36, 4.08, 21.69, 14.50, 3.60, 9.40, 8.59, 6.82, 10.85])
CARBONFACT_ASSEMBLY_GHG = np.array([3.18, 2.24, 2.68, 0.26, 6.30, 2.60, 0.42, 0.26, 0.42, 0.42, 2.60, 0.33, 0.09, 0.15, 4.35])


def _bounded_normal(rng, mean, sd, low, high, n):
    vals = rng.normal(mean, sd, n)
    return np.clip(vals, low, high)


def _generate_ml_training_data(n=12000, seed=42):
    """Build a real-data-calibrated hybrid training set.

    The previous model sampled almost every variable from broad arbitrary uniform
    ranges. This generator instead bootstraps around published plant measurements,
    open textile-LCA process references and the project's traceable fibre factors.
    Small stochastic augmentation is retained to provide enough combinations for a
    hackathon-scale classifier; therefore this is *not* a purely primary-factory
    dataset and must not be described as one.
    """
    rng = np.random.default_rng(seed)

    # India grid-screening factor is centered on CEA v21; facility overrides are
    # represented by realistic variation around the national baseline.
    grid = np.clip(rng.normal(CEA_GRID_AVERAGE_KG_PER_KWH, 0.10, n), 0.25, 1.05)

    # Fibre burden comes from the source-traceable factors already used by CHAKRA.
    fiber_values = np.array([v["co2"] for v in FIBER_FACTORS.values()], dtype=float)
    fiber = rng.choice(fiber_values, n, replace=True)

    # Electricity SEC is calibrated to actual textile-plant measurements from
    # Palamutcu (Energy, 2010), with controlled augmentation to cover efficient and
    # inefficient factories without reverting to arbitrary 0-20 kWh/kg uniforms.
    def sec_aug(name, sigma_frac=0.22, low_mult=0.45, high_mult=2.5):
        lo, hi = PALAMUTCU_SEC[name]
        base = rng.uniform(lo, hi, n)
        vals = base * rng.lognormal(mean=0.0, sigma=sigma_frac, size=n)
        return np.clip(vals, lo*low_mult, hi*high_mult)

    spin = sec_aug("spin", 0.23, 0.45, 2.2)
    weave = sec_aug("weave", 0.28, 0.40, 3.0)
    wet_elec = sec_aug("wet_elec", 0.35, 0.35, 4.0)
    sew = sec_aug("sew", 0.40, 0.35, 5.0)

    # Wet-processing water/chemical consumption comes from an 18-factory survey.
    water = _bounded_normal(rng, PLOS_WET_PROCESS["water_mean"], PLOS_WET_PROCESS["water_sd"],
                            PLOS_WET_PROCESS["water_min"], PLOS_WET_PROCESS["water_max"], n)
    # Chemical paper reports range + mean, so use a beta distribution scaled to
    # that observed range and shifted to approximately reproduce the mean.
    chem01 = rng.beta(3.0, 2.1, n)
    chem = PLOS_WET_PROCESS["chem_min"] + chem01*(PLOS_WET_PROCESS["chem_max"]-PLOS_WET_PROCESS["chem_min"])

    # Thermal energy is retained because modern dyehouses differ materially in
    # boiler/heat source. Distribution is constrained relative to wet processing,
    # rather than generated independently across an arbitrary huge range.
    wet_heat = np.clip(rng.lognormal(np.log(3.0), 0.55, n), 0.3, 18.0)
    wet_heat_factor = np.clip(rng.normal(0.32, 0.17, n), 0.02, 0.80)

    # Material loss is anchored to Carbonfact assembly Input-required values:
    # observed ratios imply roughly 10-22% fabric losses for common garments.
    waste = np.clip(rng.normal(0.14, 0.045, n), 0.01, 0.25)
    packaging = np.clip(rng.normal(0.025, 0.012, n), 0.003, 0.09)
    stress = rng.choice(np.array([1.0, 1.5, 2.0, 3.0, 4.0, 5.0]), n, p=[.10,.15,.22,.23,.18,.12])

    # Deterministic physical burden from the same transparent inputs used at
    # inference time.
    material_burden = fiber
    spinning_burden = spin * grid
    weaving_physical = weave * grid
    dyeing_burden = wet_elec * grid + wet_heat * wet_heat_factor + chem * DEFAULT_CHEMICAL_CO2_PER_KG
    assembly_physical = sew * grid + waste * DEFAULT_WASTE_CO2_PER_KG + packaging * DEFAULT_PACKAGING_CO2_PER_KG

    # Blend direct physical estimates with open Carbonfact stage-reference points.
    # This keeps labels tied to observed/reference industry stage magnitudes rather
    # than to CHAKRA's previous self-generated scoring formula.
    # Quantile-calibrate modelled stage burdens to the observed Carbonfact stage
    # distributions. Unlike random reference injection, this preserves the rank
    # relationship to measurable factory inputs while matching real/reference
    # industry magnitudes.
    def quantile_calibrate(values, refs):
        order = np.argsort(values)
        ranks = np.empty_like(order, dtype=float)
        ranks[order] = np.linspace(0.0, 1.0, len(values))
        return np.quantile(np.sort(refs), ranks)

    weave_ref = quantile_calibrate(weaving_physical, CARBONFACT_WEAVING_GHG)
    assembly_ref = quantile_calibrate(assembly_physical, CARBONFACT_ASSEMBLY_GHG)
    weaving_burden = 0.55*weaving_physical + 0.45*weave_ref
    assembly_burden = 0.65*assembly_physical + 0.35*assembly_ref

    carbon = material_burden + spinning_burden + weaving_burden + dyeing_burden + assembly_burden

    # Labels are derived from empirical cohort percentiles, not hard-coded "magic"
    # thresholds. This makes LOW/MEDIUM/HIGH relative to the real-data-calibrated
    # textile reference population used to train the model.
    water_pressure = (water / PLOS_WET_PROCESS["water_mean"]) * (stress / 3.0)
    chem_pressure = chem / PLOS_WET_PROCESS["chem_mean"]
    waste_pressure = waste / 0.14
    raw_index = (0.65*carbon + 2.0*water_pressure + 1.2*chem_pressure + 0.8*waste_pressure)
    q_low, q_high = np.quantile(raw_index, [0.35, 0.70])
    risk_y = np.where(raw_index < q_low, 0, np.where(raw_index < q_high, 1, 2)).astype(int)

    # Priority stage = highest environmental/resource burden after normalization
    # against the empirical training cohort. This is a decision label grounded in
    # the reference distribution instead of a manually preselected stage.
    stage_raw = np.column_stack([
        material_burden,
        spinning_burden,
        weaving_burden,
        dyeing_burden + 0.012*water*stress + 1.5*chem,
        assembly_burden + 3.0*waste,
    ])
    med = np.median(stage_raw, axis=0)
    med[med == 0] = 1.0
    stage_y = np.argmax(stage_raw / med, axis=1).astype(int)

    X = np.column_stack([
        grid, fiber, spin, weave, wet_elec, wet_heat, wet_heat_factor, water, chem,
        sew, waste, packaging, stress, carbon
    ])
    return X, risk_y, stage_y


def _train_xgboost_decision_models():
    X, risk_y, stage_y = _generate_ml_training_data()
    X_train, X_test, risk_train, risk_test, stage_train, stage_test = train_test_split(
        X, risk_y, stage_y, test_size=0.20, random_state=42, stratify=risk_y
    )
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train); X_test_s = scaler.transform(X_test)

    risk_model = xgb.XGBClassifier(
        objective="multi:softprob", num_class=3, n_estimators=260, max_depth=5,
        learning_rate=0.05, subsample=0.85, colsample_bytree=0.85,
        eval_metric="mlogloss", random_state=42, n_jobs=-1
    )
    stage_model = xgb.XGBClassifier(
        objective="multi:softprob", num_class=5, n_estimators=280, max_depth=5,
        learning_rate=0.05, subsample=0.85, colsample_bytree=0.85,
        eval_metric="mlogloss", random_state=43, n_jobs=-1
    )
    risk_model.fit(X_train_s, risk_train, verbose=False)
    stage_model.fit(X_train_s, stage_train, verbose=False)
    risk_acc = float(accuracy_score(risk_test, risk_model.predict(X_test_s)))
    stage_acc = float(accuracy_score(stage_test, stage_model.predict(X_test_s)))
    print(f"[CHAKRA-AI] XGBoost real-data-calibrated hybrid holdout: risk_acc={risk_acc:.4f}, stage_acc={stage_acc:.4f}; not external factory accuracy")
    return risk_model, stage_model, scaler, risk_acc, stage_acc


XGB_RISK_MODEL, XGB_STAGE_MODEL, XGB_SCALER, XGB_RISK_ACC, XGB_STAGE_ACC = _train_xgboost_decision_models()


def _ml_action_for_stage(stage: str) -> dict:
    actions = {
        "Raw Materials": {
            "priority_action": "Review fibre sourcing and compare lower-impact verified material alternatives.",
            "why": "The ML model identifies embedded raw-material impact as the strongest sustainability-risk driver.",
        },
        "Spinning": {
            "priority_action": "Prioritize spinning-motor efficiency, load optimization and electricity sourcing.",
            "why": "The ML model identifies spinning energy intensity as the strongest controllable risk driver.",
        },
        "Weaving": {
            "priority_action": "Audit loom efficiency, idle power and production scheduling before other CAPEX actions.",
            "why": "The ML model identifies weaving energy intensity as the highest-priority process risk.",
        },
        "Dyeing & Washing": {
            "priority_action": "Prioritize wet-process heat, liquor ratio, water reuse and chemical dosing optimization.",
            "why": "The ML model identifies dyeing/washing as the dominant sustainability-risk stage.",
        },
        "Cut & Sew": {
            "priority_action": "Prioritize cutting yield, fabric-scrap reduction and assembly energy efficiency.",
            "why": "The ML model identifies cut-and-sew waste/energy as the highest-priority risk stage.",
        },
    }
    return actions[stage]


def _operational_assessment(data: "LCAInput", fiber: dict, stage_emissions: dict,
                            carbon_total: float, ai_decision: dict) -> dict:
    """Apply the pre-existing CHAKRA stage rules independently of money/legal claims."""
    w = data.weight_kg
    violations = []

    def add_numeric(stage: str, metric: str, actual: float, threshold: float,
                    unit: str, emission_key: str, reason: str, action: str) -> None:
        if actual <= threshold:
            return
        emissions = float(stage_emissions[emission_key])
        violations.append({
            "stage": stage,
            "metric": metric,
            "actual": round(actual, 4),
            "threshold": threshold,
            "unit": unit,
            "excess_percent": round(((actual / threshold) - 1.0) * 100.0, 1),
            "emissions_kgco2e": round(emissions, 2),
            "emissions_share_percent": round((emissions / carbon_total) * 100.0, 1) if carbon_total else 0.0,
            "reason": reason,
            "action": action,
        })

    if data.fiber in {1, 4}:
        emissions = float(stage_emissions["material"])
        violations.append({
            "stage": "Raw Materials",
            "metric": "material selection",
            "actual": fiber["name"],
            "threshold": "lower-impact verified alternative where product requirements allow",
            "unit": "category",
            "excess_percent": None,
            "emissions_kgco2e": round(emissions, 2),
            "emissions_share_percent": round((emissions / carbon_total) * 100.0, 1) if carbon_total else 0.0,
            "reason": "The selected virgin material increases the batch's embedded raw-material footprint.",
            "action": "Review verified recycled or lower-impact fibre alternatives that meet the product specification.",
        })

    add_numeric(
        "Spinning", "electricity intensity", data.spin_kwh / w,
        OPERATIONAL_THRESHOLDS["spinning_kwh_per_kg"], "kWh/kg", "spinning",
        "Spinning electricity intensity exceeds the configured CHAKRA operational threshold.",
        "Audit spinning-motor efficiency, idle loads, machine loading and electricity sourcing.",
    )
    add_numeric(
        "Weaving", "electricity intensity", data.weave_kwh / w,
        OPERATIONAL_THRESHOLDS["weaving_kwh_per_kg"], "kWh/kg", "weaving",
        "Weaving electricity intensity exceeds the configured CHAKRA operational threshold.",
        "Audit loom efficiency, idle power and production scheduling.",
    )
    add_numeric(
        "Dyeing & Washing", "wet-process electricity intensity", data.wet_kwh / w,
        OPERATIONAL_THRESHOLDS["wet_process_kwh_per_kg"], "kWh/kg", "dyeing",
        "Wet-process electricity intensity exceeds the configured CHAKRA operational threshold.",
        "Prioritize heat recovery, process-temperature control, liquor-ratio optimization and lower-carbon heat.",
    )
    add_numeric(
        "Cut & Sew", "fabric waste ratio", data.waste_kg / w,
        OPERATIONAL_THRESHOLDS["fabric_waste_kg_per_kg"], "kg waste/kg product", "assembly",
        "Fabric waste exceeds the configured CHAKRA operational threshold.",
        "Use marker optimization, pattern-layout improvement and segregated scrap recovery.",
    )

    violations.sort(key=lambda item: item["emissions_kgco2e"], reverse=True)

    # When a batch fails, corrective work must start with a stage that actually
    # failed. XGBoost still chooses among the failed stages using its probability
    # ranking; it may not place a passing stage ahead of a measured violation.
    failed_by_stage = {item["stage"]: item for item in violations}
    ranked_failed_stage = next(
        (stage for stage in ai_decision["top_stage_ranking"] if stage in failed_by_stage),
        violations[0]["stage"] if violations else ai_decision["priority_stage"],
    )
    plan = []
    if violations:
        first = failed_by_stage[ranked_failed_stage]
        model_action = _ml_action_for_stage(ranked_failed_stage)
        plan.append({
            "rank": 1,
            "stage": ranked_failed_stage,
            "action": model_action["priority_action"],
            "reason": (
                "XGBoost ranks this as the highest-priority stage among the stages "
                "that failed deterministic operational screening."
            ),
            "source": "XGBoost-guided failed-stage priority",
        })
        for violation in violations:
            if violation["stage"] == ranked_failed_stage:
                continue
            plan.append({
                "rank": len(plan) + 1,
                "stage": violation["stage"],
                "action": violation["action"],
                "reason": violation["reason"],
                "source": "Deterministic CHAKRA threshold violation",
            })
    else:
        plan.append({
            "rank": 1,
            "stage": ai_decision["priority_stage"],
            "action": ai_decision["priority_action"],
            "reason": ai_decision["decision_reason"],
            "source": "XGBoost monitoring priority",
        })

    return {
        "status": "FAIL" if violations else "PASS",
        "failed_stage_count": len(violations),
        "failed_stages": [item["stage"] for item in violations],
        "violations": violations,
        "action_plan": plan,
        "method": "Deterministic comparison with the existing CHAKRA operational thresholds",
        "scope": "Internal operational screening only; not legal compliance, a statutory fine or certification.",
    }


def _apply_operational_risk_guardrail(ai_decision: dict, assessment: dict) -> dict:
    """Prevent a relative-cohort ML tier from understating measured failures.

    The classifier's LOW/MEDIUM/HIGH output is relative to its hybrid training
    cohort. Deterministic failures are direct comparisons with disclosed CHAKRA
    thresholds. The user-facing decision tier therefore uses the stricter of the
    two, while retaining the raw model result and confidence for transparency.
    """
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    model_tier = ai_decision["risk_tier"]
    failed_count = int(assessment["failed_stage_count"])
    numeric_excesses = [
        float(item["excess_percent"])
        for item in assessment["violations"]
        if item.get("excess_percent") is not None
    ]
    max_excess = max(numeric_excesses, default=0.0)
    if failed_count >= 2 or max_excess >= 50.0:
        operational_floor = "HIGH"
    elif failed_count == 1:
        operational_floor = "MEDIUM"
    else:
        operational_floor = "LOW"

    effective_tier = max((model_tier, operational_floor), key=rank.get)
    guardrail_applied = rank[effective_tier] > rank[model_tier]
    ai_decision.update({
        "model_risk_tier": model_tier,
        "model_risk_confidence": ai_decision["risk_confidence"],
        "model_risk_probabilities": ai_decision["risk_probabilities"],
        "risk_tier": effective_tier,
        # Backward-compatible numeric field. It always applies to model_risk_tier,
        # never to a guardrail-raised decision tier.
        "risk_confidence": ai_decision["risk_confidence"],
        "risk_confidence_applies_to": "model_risk_tier",
        "decision_tier_confidence": None if guardrail_applied else ai_decision["risk_confidence"],
        "risk_guardrail_applied": guardrail_applied,
        "operational_risk_floor": operational_floor,
        "risk_basis": (
            "Deterministic operational failures raised the decision tier above the "
            "relative-cohort XGBoost tier."
            if guardrail_applied else
            "Decision tier matches or exceeds the deterministic operational floor."
        ),
    })
    return ai_decision

# ═══════════════════════════════════════════════════════════════════════════════
#  SECURE-BY-DESIGN LAYER
#  - Real server authentication (Argon2id password hashing)
#  - HttpOnly session cookies + CSRF tokens
#  - RBAC / least privilege + separation of duties
#  - Complete mediation on every sensitive API endpoint
#  - Fail-secure behavior (no offline/mock authorization path)
#  - Tenant/factory isolation
#  - Audit logging, login throttling and temporary account lockout
#  - Trusted hosts, restrictive CORS, security headers and request-size limits
#  - Ed25519 signed, server-derived passports with revocation support
# ═══════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("CHAKRA_DB_PATH", str(DATA_DIR / "chakra_security.db")))
KEY_PATH = Path(os.getenv("CHAKRA_SIGNING_KEY_PATH", str(DATA_DIR / "passport_ed25519.pem")))
SESSION_TTL_SECONDS = int(os.getenv("CHAKRA_SESSION_TTL", "28800"))  # 8 hours absolute lifetime
SESSION_IDLE_SECONDS = int(os.getenv("CHAKRA_SESSION_IDLE", "3600"))  # 1 hour idle timeout
COOKIE_SECURE = os.getenv("CHAKRA_COOKIE_SECURE", "0") == "1"
MAX_BODY_BYTES = max(1024, int(os.getenv("CHAKRA_MAX_BODY_BYTES", str(1024 * 1024))))

# Rate-limit and authentication backoff settings are deployment-configurable.
LOGIN_IP_LIMIT = max(1, int(os.getenv("CHAKRA_LOGIN_IP_LIMIT", "10")))
LOGIN_IP_WINDOW = max(1, int(os.getenv("CHAKRA_LOGIN_IP_WINDOW", "300")))
PUBLIC_API_LIMIT = max(1, int(os.getenv("CHAKRA_PUBLIC_API_LIMIT", "60")))
PUBLIC_API_WINDOW = max(1, int(os.getenv("CHAKRA_PUBLIC_API_WINDOW", "60")))
AUTH_API_LIMIT = max(1, int(os.getenv("CHAKRA_AUTH_API_LIMIT", "180")))
AUTH_API_WINDOW = max(1, int(os.getenv("CHAKRA_AUTH_API_WINDOW", "60")))
WEB_RATE_LIMIT = max(1, int(os.getenv("CHAKRA_WEB_RATE_LIMIT", "240")))
WEB_RATE_WINDOW = max(1, int(os.getenv("CHAKRA_WEB_RATE_WINDOW", "60")))
AUTH_BACKOFF_BASE = max(1, int(os.getenv("CHAKRA_AUTH_BACKOFF_BASE", "2")))
AUTH_BACKOFF_MAX = max(AUTH_BACKOFF_BASE, int(os.getenv("CHAKRA_AUTH_BACKOFF_MAX", "900")))

ALLOWED_ROLES = {
    "Production Manager",
    "Sustainability Officer",
    "Compliance Auditor",
    "Security Admin",
}
CALCULATOR_ROLES = {"Production Manager", "Sustainability Officer"}
AUDITOR_ROLES = {"Compliance Auditor"}
ADMIN_ROLES = {"Security Admin"}

# Explicit origins only. Same-origin requests do not need CORS at all.
allowed_origins = [x.strip().rstrip("/") for x in os.getenv(
    "CHAKRA_ALLOWED_ORIGINS",
    "http://127.0.0.1:8001,http://localhost:8001"
).split(",") if x.strip()]
allowed_origin_set = set(allowed_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
allowed_hosts = [x.strip() for x in os.getenv(
    "CHAKRA_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver"
).split(",") if x.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db() -> None:
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL,
            factory TEXT NOT NULL,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            ip_hash TEXT,
            ua_hash TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

        CREATE TABLE IF NOT EXISTS calculations (
            calculation_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            factory TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            input_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'draft',
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at INTEGER,
            rejection_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_calc_factory_status ON calculations(factory, review_status);

        CREATE TABLE IF NOT EXISTS passports (
            passport_id TEXT PRIMARY KEY,
            calculation_id TEXT NOT NULL UNIQUE REFERENCES calculations(calculation_id),
            issuer_user_id INTEGER NOT NULL REFERENCES users(id),
            issued_at INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            signature_b64 TEXT NOT NULL,
            revoked_at INTEGER,
            revoked_by INTEGER REFERENCES users(id),
            revocation_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            event TEXT NOT NULL,
            user_id INTEGER,
            email TEXT,
            ip_hash TEXT,
            success INTEGER NOT NULL,
            details TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
        """)
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))


def _load_or_create_signing_key() -> Ed25519PrivateKey:
    if KEY_PATH.exists():
        try:
            return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
        except Exception as exc:
            raise RuntimeError(f"Passport signing key could not be loaded: {exc}")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    KEY_PATH.write_bytes(pem)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    print(f"[Chakra-AI] Generated local Ed25519 signing key at {KEY_PATH}. Protect this file in deployment.")
    return key


_init_db()
SIGNING_PRIVATE_KEY = _load_or_create_signing_key()
SIGNING_PUBLIC_KEY = SIGNING_PRIVATE_KEY.public_key()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For unless a trusted reverse proxy is configured externally.
    return request.client.host if request.client else "unknown"


def _ip_hash(request: Request) -> str:
    return _sha256_text(_client_ip(request))[:24]


def _audit(event: str, request: Request, success: bool, user_id: Optional[int] = None,
           email: Optional[str] = None, details: Optional[str] = None) -> None:
    safe_details = (details or "")[:1000]
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO audit_log(ts,event,user_id,email,ip_hash,success,details) VALUES(?,?,?,?,?,?,?)",
                (int(time.time()), event, user_id, email, _ip_hash(request), 1 if success else 0, safe_details),
            )
    except Exception:
        # Audit failure must never accidentally grant access.
        pass


# ── Layered rate limits ────────────────────────────────────────────────────────
_rate_store: dict[tuple[str, str], list[float]] = defaultdict(list)

def _rate_limit_for(path: str) -> tuple[str, int, int]:
    """Return bucket name, request limit and window seconds for an endpoint class."""
    if path.endswith("/auth/login"):
        return "login", LOGIN_IP_LIMIT, LOGIN_IP_WINDOW
    if re.fullmatch(r"/api/v2/passports/DPP-[A-F0-9]{20}/verify", path):
        return "public-api", PUBLIC_API_LIMIT, PUBLIC_API_WINDOW
    if path.startswith("/api/"):
        return "authenticated-api", AUTH_API_LIMIT, AUTH_API_WINDOW
    return "web", WEB_RATE_LIMIT, WEB_RATE_WINDOW


@app.middleware("http")
async def secure_request_middleware(request: Request, call_next):
    # Keep the exposed HTTP surface deliberately small.
    if request.method not in {"GET", "HEAD", "POST", "OPTIONS"}:
        return JSONResponse(status_code=405, content={"detail": "Method not allowed."})

    # Browser state-changing requests must come from an explicitly trusted origin.
    # Non-browser clients may omit Origin; authentication + CSRF still apply.
    if request.method == "POST" and request.url.path.startswith("/api/"):
        origin = (request.headers.get("origin") or "").rstrip("/")
        if origin and origin not in allowed_origin_set:
            _audit("origin_rejected", request, False, details=origin[:200])
            return JSONResponse(status_code=403, content={"detail": "Origin not allowed."})

    # Request size guard before JSON parsing. Enforce both declared and actual body size
    # so chunked/missing Content-Length requests cannot bypass the limit.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})
    if request.method == "POST" and request.url.path.startswith("/api/"):
        raw_body = await request.body()
        if len(raw_body) > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        if raw_body:
            content_type = (request.headers.get("content-type") or "").lower()
            if "application/json" not in content_type:
                return JSONResponse(status_code=415, content={"detail": "Content-Type must be application/json."})

    ip = _client_ip(request)
    bucket_name, limit, window = _rate_limit_for(request.url.path)
    key = (ip, bucket_name)
    now = time.time()
    bucket = [t for t in _rate_store[key] if now - t < window]
    if len(bucket) >= limit:
        if request.url.path.startswith("/api/"):
            _audit("rate_limit", request, False, details=request.url.path)
        retry_after = max(1, int(window - (now - min(bucket)) + 0.999))
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Retry later."},
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)
    _rate_store[key] = bucket

    request.state.request_id = secrets.token_hex(8)
    response = await call_next(request)

    # Browser hardening. CSP must allow the existing demo's explicitly used CDNs/inline code.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net "
        "https://cdnjs.cloudflare.com https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https://unpkg.com https://*.basemaps.cartocdn.com; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTHENTICATION / AUTHORIZATION
# ═══════════════════════════════════════════════════════════════════════════════

class StrictAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class LoginRequest(StrictAPIModel):
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        value = v.strip().lower()
        if not EMAIL_RE.fullmatch(value):
            raise ValueError("Invalid email format")
        return value


class LCAInput(StrictAPIModel):
    state:        str   = Field(..., description="Indian state/UT name", max_length=64)
    fiber:        int   = Field(..., ge=1, le=10)
    weight_kg:    float = Field(..., gt=0,  le=500000)
    spin_kwh:     float = Field(..., ge=0,  le=5000000)
    weave_kwh:    float = Field(..., ge=0,  le=5000000)
    wet_kwh:      float = Field(..., ge=0,  le=5000000, description="Wet-process electricity only")
    water_liters: float = Field(..., ge=0,  le=50000000)
    chemicals_kg: float = Field(..., ge=0,  le=500000)
    sew_kwh:      float = Field(..., ge=0,  le=2000000)
    waste_kg:     float = Field(..., ge=0,  le=200000)
    packaging_kg: float = Field(..., ge=0,  le=100000)

    # Optional higher-quality factor overrides. Every override requires provenance.
    electricity_factor_override: Optional[float] = Field(None, ge=0, le=2.0)
    electricity_factor_source: Optional[str] = Field(None, max_length=300)
    fiber_factor_override: Optional[float] = Field(None, ge=0, le=100.0)
    fiber_factor_source: Optional[str] = Field(None, max_length=300)
    wet_heat_kwh: float = Field(0.0, ge=0, le=10000000, description="Separate thermal energy, kWh_th")
    wet_heat_factor: Optional[float] = Field(None, ge=0, le=2.0, description="kgCO2e per kWh_th")
    wet_heat_factor_source: Optional[str] = Field(None, max_length=300)
    chemical_factor_override: Optional[float] = Field(None, ge=0, le=50.0)
    chemical_factor_source: Optional[str] = Field(None, max_length=300)
    waste_factor_override: Optional[float] = Field(None, ge=0, le=20.0)
    waste_factor_source: Optional[str] = Field(None, max_length=300)
    packaging_factor_override: Optional[float] = Field(None, ge=0, le=20.0)
    packaging_factor_source: Optional[str] = Field(None, max_length=300)

    # Optional sourced commercial scenario inputs. These are not statutory.
    buyer_benchmark_kgco2e_per_kg: Optional[float] = Field(None, gt=0, le=100.0)
    buyer_benchmark_source: Optional[str] = Field(None, max_length=300)
    carbon_price_inr_per_tonne: Optional[float] = Field(None, ge=0, le=1000000)
    ccts_target_intensity: Optional[float] = Field(None, gt=0, le=100.0)
    ccts_target_source: Optional[str] = Field(None, max_length=300)
    ccts_price_inr_per_tonne: Optional[float] = Field(None, ge=0, le=1000000)

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        if v not in STATE_ECOLOGY:
            raise ValueError("Unknown Indian state/UT")
        return v

    @model_validator(mode="after")
    def validate_process_ratios_and_sources(self):
        w = self.weight_kg
        ratio_limits = {
            "spin_kwh": (self.spin_kwh / w, 15.0), "weave_kwh": (self.weave_kwh / w, 15.0),
            "wet_kwh": (self.wet_kwh / w, 40.0), "water_liters": (self.water_liters / w, 400.0),
            "chemicals_kg": (self.chemicals_kg / w, 0.5), "sew_kwh": (self.sew_kwh / w, 10.0),
            "waste_kg": (self.waste_kg / w, 0.6), "packaging_kg": (self.packaging_kg / w, 0.3),
            "wet_heat_kwh": (self.wet_heat_kwh / w, 80.0),
        }
        for field_name, (ratio, max_ratio) in ratio_limits.items():
            if ratio > max_ratio:
                raise ValueError(f"{field_name} is outside the accepted screening range")

        pairs = [
            (self.electricity_factor_override, self.electricity_factor_source, "electricity factor"),
            (self.fiber_factor_override, self.fiber_factor_source, "fiber factor"),
            (self.chemical_factor_override, self.chemical_factor_source, "chemical factor"),
            (self.waste_factor_override, self.waste_factor_source, "waste factor"),
            (self.packaging_factor_override, self.packaging_factor_source, "packaging factor"),
        ]
        for value, source, label in pairs:
            if value is not None and not (source and source.strip()):
                raise ValueError(f"A provenance/source note is required for the {label} override")
        if self.wet_heat_kwh > 0 and (self.wet_heat_factor is None or not (self.wet_heat_factor_source and self.wet_heat_factor_source.strip())):
            raise ValueError("wet_heat_kwh requires wet_heat_factor and wet_heat_factor_source")
        if self.buyer_benchmark_kgco2e_per_kg is not None and not (self.buyer_benchmark_source and self.buyer_benchmark_source.strip()):
            raise ValueError("buyer benchmark requires buyer_benchmark_source")
        if self.buyer_benchmark_kgco2e_per_kg is None and (
            self.buyer_benchmark_source or self.carbon_price_inr_per_tonne is not None
        ):
            raise ValueError("buyer benchmark source/price requires buyer_benchmark_kgco2e_per_kg")
        if self.ccts_target_intensity is not None and not (self.ccts_target_source and self.ccts_target_source.strip()):
            raise ValueError("CCTS target scenario requires ccts_target_source")
        if self.ccts_target_intensity is None and (
            self.ccts_target_source or self.ccts_price_inr_per_tonne is not None
        ):
            raise ValueError("CCTS target source/price requires ccts_target_intensity")
        return self


class CalculationRef(StrictAPIModel):
    calculation_id: str = Field(..., min_length=12, max_length=64, pattern=r"^CAL-[A-F0-9]{24}$")


class RejectRequest(StrictAPIModel):
    reason: str = Field(..., min_length=5, max_length=500)


class RevokeRequest(StrictAPIModel):
    reason: str = Field(..., min_length=5, max_length=500)


def _session_from_request(request: Request) -> Optional[dict]:
    raw = request.cookies.get("chakra_session")
    if not raw or len(raw) < 32 or len(raw) > 256:
        return None
    token_hash = _sha256_text(raw)
    now = int(time.time())
    current_ua_hash = _sha256_text(request.headers.get("user-agent", ""))[:24]
    with _db() as conn:
        row = conn.execute("""
            SELECT s.token_hash,s.user_id,s.csrf_token,s.expires_at,s.last_seen,s.ua_hash,
                   u.email,u.name,u.factory,u.role,u.active
            FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>?
        """, (token_hash, now)).fetchone()
        if not row or not row["active"]:
            return None
        if row["last_seen"] and now - int(row["last_seen"]) > SESSION_IDLE_SECONDS:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            return None
        if row["ua_hash"] and not secrets.compare_digest(row["ua_hash"], current_ua_hash):
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            return None
        conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, token_hash))
        return dict(row)


def current_user(request: Request) -> dict:
    session = _session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return session


def require_role(*roles: str):
    allowed = set(roles)
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="You are not authorized to perform this action.")
        return user
    return dep


def require_csrf(request: Request, user: dict = Depends(current_user)) -> dict:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secrets.compare_digest(supplied, user["csrf_token"]):
        _audit("csrf_rejected", request, False, user_id=user["user_id"], email=user["email"])
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    return user


def require_role_csrf(*roles: str):
    allowed = set(roles)
    def dep(request: Request, user: dict = Depends(require_csrf)) -> dict:
        if user["role"] not in allowed:
            _audit("rbac_denied", request, False, user_id=user["user_id"], email=user["email"], details=request.url.path)
            raise HTTPException(status_code=403, detail="You are not authorized to perform this action.")
        return user
    return dep


@app.post("/api/v2/auth/login")
async def login(body: LoginRequest, request: Request):
    now = int(time.time())
    with _db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (body.email,)).fetchone()
        # Same external message whether the account exists or not.
        if not user or not user["active"]:
            PASSWORD_HASHER.hash("dummy-password-for-timing-equalization")
            _audit("login_failed", request, False, email=body.email, details="invalid_credentials")
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        if user["locked_until"] and user["locked_until"] > now:
            retry_after = max(1, int(user["locked_until"]) - now)
            _audit("login_backoff", request, False, user_id=user["id"], email=user["email"], details=f"retry_after={retry_after}")
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.", headers={"Retry-After": str(retry_after)})
        try:
            PASSWORD_HASHER.verify(user["password_hash"], body.password)
        except (VerifyMismatchError, VerificationError):
            failures = min(int(user["failed_attempts"]) + 1, 20)
            exponent = min(failures - 1, 16)
            delay = min(AUTH_BACKOFF_MAX, AUTH_BACKOFF_BASE * (2 ** exponent))
            locked_until = now + delay
            conn.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                         (failures, locked_until, user["id"]))
            # Persist failed-attempt state before raising HTTPException; sqlite's
            # connection context rolls back when an exception escapes the block.
            conn.commit()
            _audit("login_failed", request, False, user_id=user["id"], email=user["email"], details=f"invalid_credentials;backoff={delay}")
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        if PASSWORD_HASHER.check_needs_rehash(user["password_hash"]):
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (PASSWORD_HASHER.hash(body.password), user["id"]))
        conn.execute("UPDATE users SET failed_attempts=0, locked_until=0 WHERE id=?", (user["id"],))
        conn.execute("DELETE FROM sessions WHERE user_id=? OR expires_at<?", (user["id"], now))

        raw_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        token_hash = _sha256_text(raw_token)
        ua_hash = _sha256_text(request.headers.get("user-agent", ""))[:24]
        conn.execute("""
            INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at,last_seen,ip_hash,ua_hash)
            VALUES(?,?,?,?,?,?,?,?)
        """, (token_hash, user["id"], csrf_token, now+SESSION_TTL_SECONDS, now, now, _ip_hash(request), ua_hash))

    response = JSONResponse({
        "status": "success",
        "csrf_token": csrf_token,
        "user": {"name": user["name"], "email": user["email"], "factory": user["factory"], "role": user["role"]},
    })
    response.set_cookie(
        "chakra_session", raw_token,
        httponly=True, secure=COOKIE_SECURE, samesite="strict",
        max_age=SESSION_TTL_SECONDS, path="/",
    )
    _audit("login_success", request, True, user_id=user["id"], email=user["email"])
    return response


@app.get("/api/v2/auth/me")
async def me(user: dict = Depends(current_user)):
    return {"csrf_token": user["csrf_token"], "user": {"name": user["name"], "email": user["email"], "factory": user["factory"], "role": user["role"]}}


@app.post("/api/v2/auth/logout")
async def logout(request: Request, user: dict = Depends(require_csrf)):
    raw = request.cookies.get("chakra_session", "")
    if raw:
        with _db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_sha256_text(raw),))
    _audit("logout", request, True, user_id=user["user_id"], email=user["email"])
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("chakra_session", path="/")
    return response


# ═══════════════════════════════════════════════════════════════════════════════
#  CORE LCA CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_lca(data: LCAInput) -> dict:
    ecology = STATE_ECOLOGY[data.state]
    fiber = FIBER_FACTORS[data.fiber]
    w = data.weight_kg

    # Factor selection + provenance. The CEA v21 national average is an official
    # screening default; all other defaults are explicitly labelled references.
    electricity_factor = data.electricity_factor_override if data.electricity_factor_override is not None else CEA_GRID_AVERAGE_KG_PER_KWH
    electricity_source = data.electricity_factor_source.strip() if data.electricity_factor_override is not None else CEA_GRID_SOURCE
    fiber_factor = data.fiber_factor_override if data.fiber_factor_override is not None else fiber["co2"]
    fiber_source = data.fiber_factor_source.strip() if data.fiber_factor_override is not None else FIBER_REFERENCE_SOURCE
    chemical_factor = data.chemical_factor_override if data.chemical_factor_override is not None else DEFAULT_CHEMICAL_CO2_PER_KG
    chemical_source = data.chemical_factor_source.strip() if data.chemical_factor_override is not None else SECONDARY_REFERENCE_SOURCE
    waste_factor = data.waste_factor_override if data.waste_factor_override is not None else DEFAULT_WASTE_CO2_PER_KG
    waste_source = data.waste_factor_source.strip() if data.waste_factor_override is not None else SECONDARY_REFERENCE_SOURCE
    packaging_factor = data.packaging_factor_override if data.packaging_factor_override is not None else DEFAULT_PACKAGING_CO2_PER_KG
    packaging_source = data.packaging_factor_source.strip() if data.packaging_factor_override is not None else SECONDARY_REFERENCE_SOURCE

    anomalies = []
    if (data.spin_kwh / w) < MIN_SPIN_RATIO:
        anomalies.append(f"Spinning energy ({data.spin_kwh/w:.2f} kWh/kg) is below the screening plausibility floor ({MIN_SPIN_RATIO})")
    if (data.weave_kwh / w) < MIN_WEAVE_RATIO:
        anomalies.append(f"Weaving energy ({data.weave_kwh/w:.2f} kWh/kg) is below the screening plausibility floor ({MIN_WEAVE_RATIO})")
    if data.chemicals_kg > 0 and (data.chemicals_kg / w) < MIN_CHEM_RATIO:
        anomalies.append(f"Chemical ratio ({data.chemicals_kg/w:.4f}) is unusually low for the selected wet-process workflow")
    if data.water_liters > 0 and (data.water_liters / w) < MIN_WATER_RATIO:
        anomalies.append(f"Water ratio ({data.water_liters/w:.1f} L/kg) is unusually low for the selected wet-process workflow")
    data_integrity_risk = len(anomalies) >= 2

    e_material = w * fiber_factor
    e_spinning = data.spin_kwh * electricity_factor
    e_weaving = data.weave_kwh * electricity_factor
    e_wet_electricity = data.wet_kwh * electricity_factor
    e_wet_heat = data.wet_heat_kwh * (data.wet_heat_factor or 0.0)
    e_chemicals = data.chemicals_kg * chemical_factor
    e_dyeing = e_wet_electricity + e_wet_heat + e_chemicals
    e_sewing_electricity = data.sew_kwh * electricity_factor
    e_waste = data.waste_kg * waste_factor
    e_packaging = data.packaging_kg * packaging_factor
    e_assembly = e_sewing_electricity + e_waste + e_packaging
    carbon_total = e_material + e_spinning + e_weaving + e_dyeing + e_assembly
    carbon_intensity = carbon_total / w

    total_electric_kwh = data.spin_kwh + data.weave_kwh + data.wet_kwh + data.sew_kwh
    kwh_per_kg = total_electric_kwh / w
    water_per_kg = data.water_liters / w
    chem_ratio = data.chemicals_kg / w
    waste_ratio = data.waste_kg / w
    chakra_score, subscores = _chakra_score(carbon_intensity, kwh_per_kg, water_per_kg, chem_ratio, waste_ratio, ecology["water_stress"])
    if data_integrity_risk:
        chakra_score = min(chakra_score, 30.0)

    # XGBoost is the operational decision-support brain. It interprets process
    # measurements + deterministic LCA facts; legal/LCA facts themselves remain auditable.
    ml_features = np.array([[
        electricity_factor, fiber_factor, data.spin_kwh / w, data.weave_kwh / w,
        data.wet_kwh / w, data.wet_heat_kwh / w, data.wet_heat_factor or 0.0,
        water_per_kg, chem_ratio, data.sew_kwh / w, waste_ratio, data.packaging_kg / w,
        ecology["water_stress"], carbon_intensity
    ]])
    ml_scaled = XGB_SCALER.transform(ml_features)
    risk_probs = XGB_RISK_MODEL.predict_proba(ml_scaled)[0]
    stage_probs = XGB_STAGE_MODEL.predict_proba(ml_scaled)[0]
    risk_idx = int(np.argmax(risk_probs)); stage_idx = int(np.argmax(stage_probs))
    risk_label = ML_RISK_LABELS[risk_idx]; priority_stage = ML_STAGE_LABELS[stage_idx]
    stage_rank = np.argsort(stage_probs)[::-1]
    action = _ml_action_for_stage(priority_stage)
    ml_explanation = None
    if explain_stage_prediction is not None:
        try:
            ml_explanation = explain_stage_prediction(
                XGB_STAGE_MODEL,
                ml_features,
                ml_scaled,
                stage_idx,
                priority_stage,
            )
        except Exception:
            ml_explanation = None

    ai_decision = {
        "engine": "XGBoost decision-support engine",
        "risk_tier": risk_label,
        "risk_confidence": round(float(risk_probs[risk_idx]), 4),
        "risk_probabilities": {ML_RISK_LABELS[i]: round(float(risk_probs[i]), 4) for i in range(3)},
        "priority_stage": priority_stage,
        "stage_confidence": round(float(stage_probs[stage_idx]), 4),
        "stage_probabilities": {ML_STAGE_LABELS[i]: round(float(stage_probs[i]), 4) for i in range(5)},
        "top_stage_ranking": [ML_STAGE_LABELS[int(i)] for i in stage_rank[:3]],
        "priority_action": action["priority_action"],
        "decision_reason": action["why"],
        "decision_scope": "Operational sustainability decision support; does not replace source-traceable LCA or legal/scheme verification.",
        "ml_explanation": ml_explanation,
    }

    stage_emissions = {
        "material": e_material,
        "spinning": e_spinning,
        "weaving": e_weaving,
        "dyeing": e_dyeing,
        "assembly": e_assembly,
    }
    operational_assessment = _operational_assessment(
        data, fiber, stage_emissions, carbon_total, ai_decision
    )
    ai_decision = _apply_operational_risk_guardrail(ai_decision, operational_assessment)

    buyer = None
    if data.buyer_benchmark_kgco2e_per_kg is not None:
        buyer = _reference_benchmark_scenario(
            carbon_intensity,
            w,
            data.buyer_benchmark_kgco2e_per_kg,
            data.buyer_benchmark_source,
            data.carbon_price_inr_per_tonne,
        )

    ccts = None
    if data.ccts_target_intensity is not None:
        ccts = _ccts_awareness_scenario(
            carbon_intensity,
            w,
            data.ccts_target_intensity,
            data.ccts_target_source,
            data.ccts_price_inr_per_tonne,
        )

    user_verified = sum(x is not None for x in [
        data.electricity_factor_override, data.fiber_factor_override, data.chemical_factor_override,
        data.waste_factor_override, data.packaging_factor_override,
    ]) + (1 if data.wet_heat_kwh > 0 and data.wet_heat_factor is not None else 0)
    if user_verified >= 5:
        quality_grade = "A"
    elif user_verified >= 2:
        quality_grade = "B"
    else:
        quality_grade = "C"

    factor_provenance = {
        "electricity": {"value": electricity_factor, "unit": "kgCO2e/kWh", "source": electricity_source,
                        "quality": "official_default" if data.electricity_factor_override is None else "user_verified_override"},
        "fiber": {"value": fiber_factor, "unit": "kgCO2e/kg fiber", "source": fiber_source,
                  "quality": "screening_reference" if data.fiber_factor_override is None else "user_verified_override"},
        "thermal_heat": {"value": data.wet_heat_factor, "unit": "kgCO2e/kWh_th", "source": data.wet_heat_factor_source,
                         "quality": "excluded_not_provided" if data.wet_heat_kwh == 0 else "user_verified_override"},
        "chemicals": {"value": chemical_factor, "unit": "kgCO2e/kg", "source": chemical_source,
                      "quality": "screening_reference" if data.chemical_factor_override is None else "user_verified_override"},
        "waste": {"value": waste_factor, "unit": "kgCO2e/kg", "source": waste_source,
                  "quality": "screening_reference" if data.waste_factor_override is None else "user_verified_override"},
        "packaging": {"value": packaging_factor, "unit": "kgCO2e/kg", "source": packaging_source,
                      "quality": "screening_reference" if data.packaging_factor_override is None else "user_verified_override"},
        "water": {"value": 0.0, "unit": "kgCO2e/L", "source": "No generic water-to-carbon factor applied; water is reported separately as resource use",
                  "quality": "double_counting_avoided"},
    }

    dpp_readiness = {
        "record_type": "CHAKRA ESPR/DPP readiness record",
        "signed_unique_identifier": True,
        "traceable_calculation_id": True,
        "factor_provenance_recorded": True,
        "independent_review_required_before_signature": True,
        "product_specific_espr_rules_verified": False,
        "status": "technical_readiness_only",
        "note": "ESPR requirements are product-specific and progressively adopted. This record is not EU/government certification.",
    }

    data = {
        "chakra_score": chakra_score,
        "bharat_score": chakra_score,  # legacy UI alias; same transparent internal KPI
        "score_label": "CHAKRA Sustainability KPI (internal, non-regulatory)",
        "score_weights": {"carbon": 0.40, "water": 0.25, "energy": 0.20, "chemicals": 0.10, "waste": 0.05},
        "score_subscores": subscores,
        "carbon_total_kg": round(carbon_total, 2),
        "carbon_intensity": round(carbon_intensity, 3),
        "stages": {"material": round(e_material,2), "spinning": round(e_spinning,2), "weaving": round(e_weaving,2),
                   "dyeing": round(e_dyeing,2), "assembly": round(e_assembly,2), "thermal_heat": round(e_wet_heat,2),
                   "chemicals": round(e_chemicals,2), "waste": round(e_waste,2), "packaging": round(e_packaging,2)},
        "kwh_per_kg": round(kwh_per_kg,2), "water_per_kg": round(water_per_kg,2),
        "chem_ratio": round(chem_ratio,4), "waste_ratio": round(waste_ratio,4),
        "grid_factor": electricity_factor, "grid_factor_source": electricity_source,
        "fiber_name": fiber["name"], "fiber_co2_factor": fiber_factor,
        "data_integrity_risk": data_integrity_risk, "greenwash_risk": data_integrity_risk,
        "anomalies": anomalies, "anomaly_method": "rule-based plausibility checks; does not infer intent",
        "ai_decision": ai_decision,
        "operational_assessment": operational_assessment,
        "data_quality_grade": quality_grade, "factor_provenance": factor_provenance,
        "screening_boundary": "Raw material + reported grid electricity + optional sourced thermal heat + screening/reference chemical, waste and packaging factors. Water reported as resource use without generic CO2 conversion.",
        "reference_benchmark": buyer,
        "buyer_benchmark": buyer,
        "ccts_scenario": ccts,
        "espr_dpp_readiness": dpp_readiness,
        "legal_note": "Decision-support screening only; not ISO-verified LCA, EU customs clearance, CBAM liability, CCTS certificate or government certification.",
        # Backward-compatible aliases, explicitly scenario-based rather than legal claims.
        "is_eu_compliant": bool(buyer["pass"]) if buyer else None,
        "eu_tax_exposure_inr": float(buyer["scenario_exposure_inr"]) if buyer else 0.0,
        "is_ccts_eligible": False,
        "ccts_revenue_inr": float(ccts["scenario_value_inr"]) if ccts else 0.0,
    }
    return {
        "model": {
            "type": "XGBoost decision-support engine",
            "authoritative": False,
            "decision_authority": "operational_priority_only",
            "hybrid_holdout_risk_accuracy": round(XGB_RISK_ACC, 4),
            "hybrid_holdout_stage_accuracy": round(XGB_STAGE_ACC, 4),
            "external_validation": False,
            "training_basis": "real-world-data-calibrated hybrid textile dataset",
            "training_sources": [
                "Carbonfact Open Source LCA Database for Footwear & Apparel v1.1.0 (2026)",
                "Palamutcu, Energy 35(7), 2010 - measured textile-stage electricity SEC",
                "Uddin et al., PLOS Sustainability and Transformation, 2023 - 18-factory wet-processing survey",
                "Central Electricity Authority CO2 Baseline Database v21.0",
                "CHAKRA source-traceable fibre factors"
            ],
            "note": "The ML engine is trained on a real-data-calibrated hybrid textile dataset using published plant measurements and open LCA references. Holdout metrics are internal validation and not real-world accuracy; external factory accuracy requires a separate pilot dataset. LCA and regulatory facts remain deterministic and source-traceable.",
        },
        "data": data,
    }


@app.post("/api/v2/calculate")
async def calculate_lca(data: LCAInput, request: Request,
                        user: dict = Depends(require_role_csrf(*CALCULATOR_ROLES))):
    result = _compute_lca(data)
    calculation_id = "CAL-" + uuid.uuid4().hex[:24].upper()
    now = int(time.time())
    with _db() as conn:
        conn.execute("""
            INSERT INTO calculations(calculation_id,user_id,factory,created_at,input_json,result_json,review_status)
            VALUES(?,?,?,?,?,?, 'draft')
        """, (calculation_id, user["user_id"], user["factory"], now,
              json.dumps(data.model_dump(), separators=(",", ":")),
              json.dumps(result, separators=(",", ":"))))
    _audit("calculation_created", request, True, user_id=user["user_id"], email=user["email"], details=calculation_id)
    return {"status": "success", "calculation_id": calculation_id, "review_status": "draft", **result}


@app.get("/api/v2/my/calculations")
async def my_calculations(request: Request,
                          user: dict = Depends(require_role(*CALCULATOR_ROLES))):
    """Return the authenticated submitter's persisted batches and any issued passport.

    This is deliberately owner-scoped: a manager/sustainability officer can only see
    calculations they personally created for their authenticated factory.
    """
    with _db() as conn:
        rows = conn.execute("""
            SELECT c.calculation_id,c.created_at,c.review_status,c.reviewed_at,c.rejection_reason,
                   c.result_json,p.passport_id,p.issued_at,p.payload_json,p.signature_b64,
                   p.revoked_at,p.revocation_reason
            FROM calculations c
            LEFT JOIN passports p ON p.calculation_id=c.calculation_id
            WHERE c.user_id=? AND c.factory=?
            ORDER BY c.created_at DESC
            LIMIT 50
        """, (user["user_id"], user["factory"])).fetchall()

    items = []
    base = str(request.base_url).rstrip("/")
    for row in rows:
        result = json.loads(row["result_json"])["data"]
        passport = None
        if row["passport_id"]:
            payload = json.loads(row["payload_json"])
            passport = {
                **payload,
                "signature": row["signature_b64"],
                "verification_url": f"{base}/api/v2/passports/{row['passport_id']}/verify",
                "revoked": bool(row["revoked_at"]),
                "revoked_at": row["revoked_at"],
                "revocation_reason": row["revocation_reason"],
            }
        items.append({
            "calculation_id": row["calculation_id"],
            "created_at": row["created_at"],
            "review_status": row["review_status"],
            "reviewed_at": row["reviewed_at"],
            "rejection_reason": row["rejection_reason"],
            "carbon_intensity": result["carbon_intensity"],
            "bharat_score": result["bharat_score"],
            "greenwash_risk": result["greenwash_risk"],
            "passport": passport,
        })
    return {"items": items}


@app.get("/api/v2/my/calculations/{calculation_id}")
async def my_calculation_detail(calculation_id: str, request: Request,
                                user: dict = Depends(require_role(*CALCULATOR_ROLES))):
    if not re.fullmatch(r"CAL-[A-F0-9]{24}", calculation_id):
        raise HTTPException(status_code=404, detail="Calculation not found.")
    with _db() as conn:
        row = conn.execute("""
            SELECT c.*,p.passport_id,p.issued_at AS passport_issued_at,p.payload_json,p.signature_b64,
                   p.revoked_at,p.revocation_reason
            FROM calculations c
            LEFT JOIN passports p ON p.calculation_id=c.calculation_id
            WHERE c.calculation_id=? AND c.user_id=? AND c.factory=?
        """, (calculation_id, user["user_id"], user["factory"])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calculation not found.")
    passport = None
    if row["passport_id"]:
        payload = json.loads(row["payload_json"])
        passport = {
            **payload,
            "signature": row["signature_b64"],
            "verification_url": str(request.base_url).rstrip("/") + f"/api/v2/passports/{row['passport_id']}/verify",
            "revoked": bool(row["revoked_at"]),
            "revoked_at": row["revoked_at"],
            "revocation_reason": row["revocation_reason"],
        }
    input_payload = json.loads(row["input_json"])
    result_payload = json.loads(row["result_json"])
    if "operational_assessment" not in result_payload.get("data", {}):
        # Backward-compatible view upgrade for calculations saved before the
        # operational output was restored. Historical records are not rewritten.
        stored_data = result_payload["data"]
        parsed_input = LCAInput(**input_payload)
        ai_decision = stored_data.get("ai_decision")
        if ai_decision:
            stored_stages = stored_data["stages"]
            stage_emissions = {key: float(stored_stages[key]) for key in ("material", "spinning", "weaving", "dyeing", "assembly")}
            result_payload["data"]["operational_assessment"] = _operational_assessment(
                parsed_input, FIBER_FACTORS[parsed_input.fiber], stage_emissions,
                float(stored_data["carbon_total_kg"]), ai_decision,
            )
        else:
            refreshed = _compute_lca(parsed_input)
            result_payload["data"]["operational_assessment"] = refreshed["data"]["operational_assessment"]
            result_payload["data"]["ai_decision"] = refreshed["data"]["ai_decision"]
    return {
        "calculation_id": row["calculation_id"],
        "created_at": row["created_at"],
        "review_status": row["review_status"],
        "reviewed_at": row["reviewed_at"],
        "rejection_reason": row["rejection_reason"],
        "input": input_payload,
        "result": result_payload,
        "passport": passport,
    }


@app.post("/api/v2/calculations/{calculation_id}/submit-review")
async def submit_review(calculation_id: str, request: Request,
                        user: dict = Depends(require_role_csrf(*CALCULATOR_ROLES))):
    with _db() as conn:
        row = conn.execute("SELECT * FROM calculations WHERE calculation_id=?", (calculation_id,)).fetchone()
        if not row or row["user_id"] != user["user_id"] or row["factory"] != user["factory"]:
            raise HTTPException(status_code=404, detail="Calculation not found.")
        if row["review_status"] not in {"draft", "rejected"}:
            raise HTTPException(status_code=409, detail="Calculation is already under review or approved.")
        result = json.loads(row["result_json"])
        if result["data"]["data_integrity_risk"]:
            raise HTTPException(status_code=403, detail="Data-integrity anomalies must be resolved before review.")
        conn.execute("UPDATE calculations SET review_status='pending_review', rejection_reason=NULL WHERE calculation_id=?", (calculation_id,))
    _audit("review_submitted", request, True, user_id=user["user_id"], email=user["email"], details=calculation_id)
    return {"status": "pending_review", "calculation_id": calculation_id}


@app.get("/api/v2/auditor/queue")
async def auditor_queue(user: dict = Depends(require_role(*AUDITOR_ROLES))):
    with _db() as conn:
        rows = conn.execute("""
            SELECT c.calculation_id,c.created_at,c.input_json,c.result_json,u.name AS submitter,u.email AS submitter_email
            FROM calculations c JOIN users u ON u.id=c.user_id
            WHERE c.factory=? AND c.review_status='pending_review' AND c.user_id<>?
            ORDER BY c.created_at ASC LIMIT 50
        """, (user["factory"], user["user_id"])).fetchall()
    items = []
    for row in rows:
        result_payload = json.loads(row["result_json"])
        if "operational_assessment" not in result_payload.get("data", {}):
            result_payload = _compute_lca(LCAInput(**json.loads(row["input_json"])))
        result = result_payload["data"]
        operational = result["operational_assessment"]
        items.append({
            "calculation_id": row["calculation_id"], "created_at": row["created_at"],
            "submitter": row["submitter"], "submitter_email": row["submitter_email"],
            "carbon_intensity": result["carbon_intensity"], "bharat_score": result["bharat_score"],
            "data_integrity_risk": result["data_integrity_risk"], "data_quality_grade": result["data_quality_grade"],
            "operational_status": operational["status"],
            "failed_stage_count": operational["failed_stage_count"],
            "failed_stages": operational["failed_stages"],
        })
    return {"items": items}


@app.post("/api/v2/auditor/calculations/{calculation_id}/reject")
async def reject_calculation(calculation_id: str, body: RejectRequest, request: Request,
                             user: dict = Depends(require_role_csrf(*AUDITOR_ROLES))):
    with _db() as conn:
        row = conn.execute("SELECT * FROM calculations WHERE calculation_id=?", (calculation_id,)).fetchone()
        if not row or row["factory"] != user["factory"] or row["user_id"] == user["user_id"]:
            raise HTTPException(status_code=404, detail="Calculation not found.")
        if row["review_status"] != "pending_review":
            raise HTTPException(status_code=409, detail="Calculation is not pending review.")
        conn.execute("""
            UPDATE calculations SET review_status='rejected',reviewed_by=?,reviewed_at=?,rejection_reason=?
            WHERE calculation_id=?
        """, (user["user_id"], int(time.time()), body.reason.strip(), calculation_id))
    _audit("review_rejected", request, True, user_id=user["user_id"], email=user["email"], details=calculation_id)
    return {"status": "rejected", "calculation_id": calculation_id}


def _canonical_json(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@app.post("/api/v2/mint-passport")
async def mint_passport(ref: CalculationRef, request: Request,
                        user: dict = Depends(require_role_csrf(*AUDITOR_ROLES))):
    with _db() as conn:
        row = conn.execute("SELECT * FROM calculations WHERE calculation_id=?", (ref.calculation_id,)).fetchone()
        if not row or row["factory"] != user["factory"] or row["user_id"] == user["user_id"]:
            raise HTTPException(status_code=404, detail="Calculation not found.")
        if row["review_status"] != "pending_review":
            raise HTTPException(status_code=409, detail="Calculation is not pending independent review.")
        existing = conn.execute("SELECT passport_id FROM passports WHERE calculation_id=?", (ref.calculation_id,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A passport already exists for this calculation.")
        inp = json.loads(row["input_json"])
        result_payload = json.loads(row["result_json"])
        if "operational_assessment" not in result_payload.get("data", {}):
            result_payload = _compute_lca(LCAInput(**inp))
        result = result_payload["data"]
        if result["data_integrity_risk"]:
            raise HTTPException(status_code=403, detail="Passport denied: unresolved data-integrity anomalies.")

        issued_at = int(time.time())
        passport_id = "DPP-" + uuid.uuid4().hex[:20].upper()
        payload = {
            "passport_id": passport_id,
            "calculation_id": ref.calculation_id,
            "factory": row["factory"],
            "batch_state": inp["state"],
            "fiber": result["fiber_name"],
            "weight_kg": inp["weight_kg"],
            "carbon_intensity": result["carbon_intensity"],
            "chakra_score": result["chakra_score"],
            "bharat_score": result["bharat_score"],
            "score_label": result["score_label"],
            "operational_status": result["operational_assessment"]["status"],
            "failed_stage_count": result["operational_assessment"]["failed_stage_count"],
            "failed_stages": result["operational_assessment"]["failed_stages"],
            "issued_at": issued_at,
            "issuer_role": user["role"],
            "document_type": "CHAKRA-AI Signed Digital Product Passport Readiness Record",
            "data_quality_grade": result["data_quality_grade"],
            "factor_provenance": result["factor_provenance"],
            "screening_boundary": result["screening_boundary"],
            "espr_dpp_readiness": result["espr_dpp_readiness"],
            "claim": "Independently reviewed factual CHAKRA calculation record. A signature attests record integrity and review; it does not convert an operational FAIL into a sustainability PASS. Technical ESPR/DPP readiness only; not EU/government certification.",
        }
        signature = SIGNING_PRIVATE_KEY.sign(_canonical_json(payload))
        signature_b64 = base64.urlsafe_b64encode(signature).decode("ascii")
        conn.execute("""
            INSERT INTO passports(passport_id,calculation_id,issuer_user_id,issued_at,payload_json,signature_b64)
            VALUES(?,?,?,?,?,?)
        """, (passport_id, ref.calculation_id, user["user_id"], issued_at,
              json.dumps(payload, separators=(",", ":")), signature_b64))
        conn.execute("""
            UPDATE calculations SET review_status='approved',reviewed_by=?,reviewed_at=?,rejection_reason=NULL
            WHERE calculation_id=?
        """, (user["user_id"], issued_at, ref.calculation_id))

    _audit("passport_minted", request, True, user_id=user["user_id"], email=user["email"], details=passport_id)
    verify_url = str(request.base_url).rstrip("/") + f"/api/v2/passports/{passport_id}/verify"
    passport_data = {**payload, "signature": signature_b64, "verification_url": verify_url}
    standards_mapping = None
    if get_standards_mapping_payload is not None:
        standards_mapping = get_standards_mapping_payload(passport_data)
    resp = {"status": "minted", "passport": passport_data}
    if standards_mapping:
        resp["standards_mapping"] = standards_mapping
    return resp


@app.get("/api/v2/passports/{passport_id}/verify")
async def verify_passport(passport_id: str, request: Request):
    if not re.fullmatch(r"DPP-[A-F0-9]{20}", passport_id):
        raise HTTPException(status_code=404, detail="Passport not found.")
    with _db() as conn:
        row = conn.execute("SELECT * FROM passports WHERE passport_id=?", (passport_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Passport not found.")
    payload = json.loads(row["payload_json"])
    try:
        signature = base64.urlsafe_b64decode(row["signature_b64"].encode("ascii"))
        SIGNING_PUBLIC_KEY.verify(signature, _canonical_json(payload))
        signature_valid = True
    except Exception:
        signature_valid = False

    passport_with_meta = {
        **payload,
        "signature": row["signature_b64"],
        "verification_url": str(request.base_url).rstrip("/") + f"/api/v2/passports/{passport_id}/verify",
        "revoked": bool(row["revoked_at"]),
        "revocation_reason": row["revocation_reason"] if row["revoked_at"] else None,
    }
    standards_mapping = None
    if get_standards_mapping_payload is not None:
        standards_mapping = get_standards_mapping_payload(passport_with_meta)

    resp = {
        "passport_id": passport_id,
        "valid": bool(signature_valid and not row["revoked_at"]),
        "signature_valid": signature_valid,
        "revoked": bool(row["revoked_at"]),
        "revocation_reason": row["revocation_reason"] if row["revoked_at"] else None,
        "passport": payload,
    }
    if standards_mapping:
        resp["standards_mapping"] = standards_mapping
    return resp


@app.get("/api/v2/passports/{passport_id}/standards")
async def passport_standards_mapping(passport_id: str, request: Request):
    if not re.fullmatch(r"DPP-[A-F0-9]{20}", passport_id):
        raise HTTPException(status_code=404, detail="Passport not found.")
    with _db() as conn:
        row = conn.execute("SELECT * FROM passports WHERE passport_id=?", (passport_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Passport not found.")
    payload = json.loads(row["payload_json"])
    passport_with_meta = {
        **payload,
        "signature": row["signature_b64"],
        "verification_url": str(request.base_url).rstrip("/") + f"/api/v2/passports/{passport_id}/verify",
        "revoked": bool(row["revoked_at"]),
        "revocation_reason": row["revocation_reason"] if row["revoked_at"] else None,
    }
    if get_standards_mapping_payload is None:
        raise HTTPException(status_code=503, detail="Standards mapping unavailable.")
    return get_standards_mapping_payload(passport_with_meta)


@app.post("/api/v2/passports/{passport_id}/revoke")
async def revoke_passport(passport_id: str, body: RevokeRequest, request: Request,
                          user: dict = Depends(require_role_csrf(*ADMIN_ROLES))):
    with _db() as conn:
        row = conn.execute("SELECT * FROM passports WHERE passport_id=?", (passport_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Passport not found.")
        if row["revoked_at"]:
            raise HTTPException(status_code=409, detail="Passport is already revoked.")
        conn.execute("UPDATE passports SET revoked_at=?,revoked_by=?,revocation_reason=? WHERE passport_id=?",
                     (int(time.time()), user["user_id"], body.reason.strip(), passport_id))
    _audit("passport_revoked", request, True, user_id=user["user_id"], email=user["email"], details=passport_id)
    return {"status": "revoked", "passport_id": passport_id}


@app.get("/api/v2/constants")
async def get_constants(user: dict = Depends(current_user)):
    return {
        "cea_grid": {"version": CEA_GRID_VERSION, "fiscal_year": CEA_GRID_FY,
                     "weighted_average_kg_per_kwh": CEA_GRID_AVERAGE_KG_PER_KWH,
                     "source": CEA_GRID_SOURCE},
        "fiber_factors": FIBER_FACTORS,
        "state_water_stress": STATE_WATER_STRESS,
        "score_method": "Transparent CHAKRA internal KPI; not official/regulatory",
        "xgboost": {"authoritative": False, "synthetic_holdout_r2": round(XGB_R2, 4), "external_validation": False},
        "eu": {"textiles_cbam_threshold": None, "espr_dpp": "product-specific progressive requirements"},
        "ccts": {"universal_textile_target": None, "requires_notified_entity_target": True},
    }


@app.get("/api/v2/security/audit")
async def recent_audit(user: dict = Depends(require_role(*ADMIN_ROLES))):
    with _db() as conn:
        rows = conn.execute("SELECT ts,event,user_id,email,ip_hash,success,details FROM audit_log ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [dict(x) for x in rows]}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", secrets.token_hex(8))
    _audit("server_error", request, False, details=f"request_id={request_id};type={type(exc).__name__}")
    return JSONResponse(status_code=500, content={"detail": "Unexpected server error.", "request_id": request_id})


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.3.0", "api_auth": "required", "session_protection": "enabled"}


# Serve the app from the same origin as the API. This avoids wildcard CORS and
# keeps HttpOnly/SameSite session cookies useful.
@app.get("/")
async def frontend():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/logo.jpeg")
async def logo():
    return FileResponse(BASE_DIR / "logo.jpeg", media_type="image/jpeg")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
