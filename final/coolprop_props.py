"""R-1234yf thermodynamic property helpers (CoolProp).

Install on the Pi:  pip3 install CoolProp --break-system-packages
"""

from __future__ import annotations

import json
import math

REFRIGERANT = "R1234yf"

try:
    from CoolProp.CoolProp import PropsSI
    COOLPROP_AVAILABLE = True
    COOLPROP_IMPORT_ERROR = None
except Exception as exc:
    PropsSI = None
    COOLPROP_AVAILABLE = False
    COOLPROP_IMPORT_ERROR = exc


def _props(output: str, T_C: float, P_Pa: float) -> float:
    """Return a CoolProp property for T (°C) and P (Pa); NaN on failure."""
    if not COOLPROP_AVAILABLE:
        return float("nan")
    try:
        return PropsSI(output, "T", T_C + 273.15, "P", P_Pa, REFRIGERANT)
    except Exception:
        return float("nan")


def build_saturation_table(
    t_min_c: float = -40.0,
    t_max_c: float = 94.0,
    t_step_c: float = 2.0,
) -> list[dict]:
    """R-1234yf saturation curve. Each row: {T °C, P kPa, h_liq/h_vap kJ/kg, s_liq/s_vap kJ/(kg·K)}."""
    if not COOLPROP_AVAILABLE:
        print(f"[CoolProp] Not available: {COOLPROP_IMPORT_ERROR}")
        return []

    rows: list[dict] = []
    T_C = t_min_c
    while T_C <= t_max_c + 1e-6:
        T_K = T_C + 273.15
        try:
            P_Pa   = PropsSI("P",   "T", T_K, "Q", 0, REFRIGERANT)
            h_liq  = PropsSI("H",   "T", T_K, "Q", 0, REFRIGERANT) / 1000.0  # J/kg → kJ/kg
            h_vap  = PropsSI("H",   "T", T_K, "Q", 1, REFRIGERANT) / 1000.0
            s_liq  = PropsSI("S",   "T", T_K, "Q", 0, REFRIGERANT) / 1000.0  # J/(kg·K) → kJ/(kg·K)
            s_vap  = PropsSI("S",   "T", T_K, "Q", 1, REFRIGERANT) / 1000.0
            rows.append({
                "T":     round(T_C, 2),
                "P":     round(P_Pa / 1000.0, 3),   # kPa
                "h_liq": round(h_liq, 3),
                "h_vap": round(h_vap, 3),
                "s_liq": round(s_liq, 4),
                "s_vap": round(s_vap, 4),
            })
        except Exception:
            pass
        T_C = round(T_C + t_step_c, 6)

    return rows


def saturation_table_payload(
    t_min_c: float = -40.0,
    t_max_c: float = 94.0,
    t_step_c: float = 2.0,
) -> str:
    """Saturation table as a compact JSON string."""
    table = build_saturation_table(t_min_c, t_max_c, t_step_c)
    return json.dumps(table, separators=(",", ":"))


def state_point_props(T_C: float, P_Pa: float) -> dict:
    """Return {h kJ/kg, s kJ/(kg·K), phase} for a (T, P) point. NaN on failure."""
    if not COOLPROP_AVAILABLE:
        return {"h": float("nan"), "s": float("nan"), "phase": "unknown"}

    T_K = T_C + 273.15
    try:
        h_J  = PropsSI("H", "T", T_K, "P", P_Pa, REFRIGERANT)
        s_J  = PropsSI("S", "T", T_K, "P", P_Pa, REFRIGERANT)
        try:
            Q = PropsSI("Q", "T", T_K, "P", P_Pa, REFRIGERANT)
            if 0.0 <= Q <= 1.0:
                phase = "two-phase"
            elif Q < 0:
                phase = "liquid"
            else:
                phase = "gas"
        except Exception:
            phase = "unknown"

        return {
            "h": round(h_J / 1000.0, 3),
            "s": round(s_J / 1000.0, 4),
            "phase": phase,
        }
    except Exception:
        return {"h": float("nan"), "s": float("nan"), "phase": "unknown"}


def state_points_payload(
    high_T: float, high_P: float,
    low_T:  float, low_P:  float,
    evap_T: float, evap_P: float,
    exv_T:  float, exv_P:  float,
) -> str:
    """Compact JSON of the four cycle state points. NaN values become null."""
    def _safe(T: float, P: float) -> dict:
        if math.isnan(T) or math.isnan(P) or P <= 0:
            return {"h": None, "s": None, "phase": "unknown"}
        props = state_point_props(T, P)
        return {
            "h":     props["h"] if not math.isnan(props["h"]) else None,
            "s":     props["s"] if not math.isnan(props["s"]) else None,
            "phase": props["phase"],
        }

    payload = {
        "HighSide":   _safe(high_T, high_P),
        "LowSide":    _safe(low_T,  low_P),
        "Evaporator": _safe(evap_T, evap_P),
        "EXV":        _safe(exv_T,  exv_P),
    }
    return json.dumps(payload, separators=(",", ":"))
