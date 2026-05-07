"""Circuit1 config — inherits everything from final/config.py and overrides only this circuit's differences."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_final_config", Path(__file__).resolve().parent.parent / "final" / "config.py"
)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("_")})

# --- Circuit1 overrides ---
CIRCUIT = "Circuit1"

SENSORS = {
    "HighSide": 0x1A,
    "LowSide": 0x4A,
    "Evaporator": 0x3A,
    "EXV": 0x5A,
}

# Circuit1 uses a capillary tube (fixed restriction). Above a certain RPM the cap
# tube overfeeds the evaporator, raising low-side pressure and reducing cooling
# capacity. Cap the PID ceiling here to keep the system in an efficient range.
VFD_MAX_RPM = 4400.0

# ADS1115 channel index (0 = A0, 1 = A1, 2 = A2, 3 = A3) for each pressure sensor.
PRESSURE_CHANNELS = {
    "HighSide":   0,
    "LowSide":    2,
    "Evaporator": 3,
    "EXV":        1,
}
