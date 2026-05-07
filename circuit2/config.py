"""Circuit2 config — inherits everything from final/config.py and overrides only this circuit's differences."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_final_config", Path(__file__).resolve().parent.parent / "final" / "config.py"
)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("_")})

# --- Circuit2 overrides ---
CIRCUIT = "Circuit2"

# Circuit2 uses a TEV (thermostatic expansion valve), which self-regulates
# superheat across the full compressor speed range. No RPM ceiling needed —
# the base VFD_MAX_RPM of 4500 RPM applies.

# TODO: replace with the addresses programmed into Circuit2's MLX90614s.
SENSORS = {
    "HighSide": 0x5D,
    "LowSide": 0x5B,
    "Evaporator": 0x5A,
    "EXV": 0x5C,
}

HMI_SETPOINT_MIN_C = 12.8  # 55°F — Circuit2 (TEV) handles lower setpoints reliably

# ADS1115 channel index (0 = A0, 1 = A1, 2 = A2, 3 = A3) for each pressure sensor.
PRESSURE_CHANNELS = {
    "HighSide":   1,
    "LowSide":    0,
    "Evaporator": 2,
    "EXV":        3,
}
