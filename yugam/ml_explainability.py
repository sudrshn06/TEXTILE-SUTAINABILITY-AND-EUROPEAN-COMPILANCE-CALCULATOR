"""
SHAP Explainability Module for CHAKRA-AI.

Provides TreeSHAP attribution for XGBoost stage prioritisation decisions.
Explanation-only: does NOT modify or replace any deterministic LCA calculations,
XGBoost predictions, or regulatory assessments.
"""

import logging
from typing import Any, Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Feature definitions aligned with the 14 training/inference features in exact order
FEATURE_DEFINITIONS: List[Dict[str, str]] = [
    {
        "key": "grid_electricity_factor",
        "name": "Grid Electricity Factor",
        "unit": "kg CO2e/kWh",
    },
    {
        "key": "raw_fiber_factor",
        "name": "Raw Fiber Factor",
        "unit": "kg CO2e/kg",
    },
    {
        "key": "spinning_electricity_intensity",
        "name": "Spinning Electricity Intensity",
        "unit": "kWh/kg",
    },
    {
        "key": "weaving_electricity_intensity",
        "name": "Weaving Electricity Intensity",
        "unit": "kWh/kg",
    },
    {
        "key": "wet_process_electricity_intensity",
        "name": "Wet-Process Electricity Intensity",
        "unit": "kWh/kg",
    },
    {
        "key": "wet_process_thermal_intensity",
        "name": "Wet-Process Thermal Intensity",
        "unit": "kWh/kg",
    },
    {
        "key": "wet_process_thermal_factor",
        "name": "Wet-Process Thermal Factor",
        "unit": "kg CO2e/kWh",
    },
    {
        "key": "water_consumption_intensity",
        "name": "Water Consumption Intensity",
        "unit": "L/kg",
    },
    {
        "key": "chemical_consumption_ratio",
        "name": "Chemical Consumption Ratio",
        "unit": "kg/kg",
    },
    {
        "key": "sewing_electricity_intensity",
        "name": "Assembly Electricity Intensity",
        "unit": "kWh/kg",
    },
    {
        "key": "fabric_waste_ratio",
        "name": "Fabric Waste Ratio",
        "unit": "kg/kg",
    },
    {
        "key": "packaging_mass_ratio",
        "name": "Packaging Mass Ratio",
        "unit": "kg/kg",
    },
    {
        "key": "regional_water_stress",
        "name": "Regional Water Stress Multiplier",
        "unit": "multiplier",
    },
    {
        "key": "batch_carbon_intensity",
        "name": "Batch Carbon Intensity",
        "unit": "kg CO2e/kg",
    },
]

_STAGE_EXPLAINER = None


def get_stage_explainer(stage_model) -> Optional[Any]:
    """Retrieve or initialize the singleton TreeExplainer for the stage model."""
    global _STAGE_EXPLAINER
    if _STAGE_EXPLAINER is None and stage_model is not None:
        try:
            import shap
            _STAGE_EXPLAINER = shap.TreeExplainer(stage_model)
        except Exception as e:
            logger.warning("Could not initialize SHAP TreeExplainer: %s", e)
            return None
    return _STAGE_EXPLAINER


def explain_stage_prediction(
    stage_model,
    ml_features_raw: np.ndarray,
    ml_scaled: np.ndarray,
    predicted_stage_idx: int,
    predicted_stage_label: str,
    top_n: int = 5,
) -> Optional[Dict[str, Any]]:
    """
    Explain the predicted priority stage using TreeSHAP.

    Extracts SHAP values strictly for the predicted class/stage.
    Preserves direction:
      - 'toward': positive SHAP value (pushes model toward this stage)
      - 'away': negative SHAP value (pushes model away from this stage)

    Calculates 'relative_influence_percent' strictly as relative model influence
    derived from absolute SHAP magnitudes.

    Returns None if explainability fails, ensuring main calculations never break.
    """
    try:
        explainer = get_stage_explainer(stage_model)
        if explainer is None:
            return None

        shap_out = explainer.shap_values(ml_scaled)

        # Handle multiclass output formats across different SHAP versions
        if isinstance(shap_out, list):
            class_shap = np.asarray(shap_out[predicted_stage_idx]).reshape(-1)
        elif isinstance(shap_out, np.ndarray):
            if shap_out.ndim == 3:
                if shap_out.shape[0] == 1 and shap_out.shape[2] == 5:
                    class_shap = shap_out[0, :, predicted_stage_idx]
                elif shap_out.shape[0] == 5:
                    class_shap = shap_out[predicted_stage_idx, 0, :]
                else:
                    class_shap = shap_out[0, :, predicted_stage_idx]
            elif shap_out.ndim == 2:
                class_shap = shap_out[0, :]
            else:
                return None
        else:
            return None

        if len(class_shap) != len(FEATURE_DEFINITIONS):
            return None

        raw_vals = ml_features_raw.reshape(-1)

        feature_entries: List[Dict[str, Any]] = []
        for idx, fdef in enumerate(FEATURE_DEFINITIONS):
            s_val = float(class_shap[idx])
            if not np.isfinite(s_val):
                continue
            direction = "toward" if s_val >= 0 else "away"
            r_val = float(raw_vals[idx])
            feature_entries.append({
                "feature": fdef["key"],
                "feature_name": fdef["name"],
                "unit": fdef["unit"],
                "value": round(r_val, 4),
                "shap_value": round(s_val, 4),
                "direction": direction,
                "_abs_shap": abs(s_val),
            })

        if not feature_entries:
            return None

        # Sort by absolute SHAP magnitude (highest influence first)
        feature_entries.sort(key=lambda x: x["_abs_shap"], reverse=True)

        total_abs_shap = sum(f["_abs_shap"] for f in feature_entries)
        if total_abs_shap <= 0:
            total_abs_shap = 1.0

        top_features = feature_entries[:top_n]
        for f in top_features:
            f["relative_influence_percent"] = round((f["_abs_shap"] / total_abs_shap) * 100.0, 1)
            del f["_abs_shap"]

        return {
            "method": "TreeSHAP",
            "target": predicted_stage_label,
            "target_stage_index": predicted_stage_idx,
            "features": top_features,
            "disclaimer": "SHAP explains ML prioritisation only. Carbon, water, energy and waste calculations remain deterministic.",
        }
    except Exception as ex:
        logger.warning("SHAP explanation generation failed: %s", ex)
        return None
