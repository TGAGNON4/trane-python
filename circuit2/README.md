## Files
- `main.py` — shim: patches `sys.path` so this directory's `config.py` shadows `final/config.py`, then executes `final/main.py`
- `config.py` — Circuit2 overrides: inherits all defaults from `final/config.py` and overrides only circuit-specific values

## Run
```bash
python main.py
```

## Dependencies
```bash
pip3 install paho-mqtt simple-pid adafruit-circuitpython-mlx90614 adafruit-circuitpython-ads1x15 CoolProp openpyxl RPi.GPIO --break-system-packages
```

## Circuit2 Overrides
All base defaults live in `final/config.py`. This directory's `config.py` overrides only:

| Setting | Circuit2 Value | Description |
|---|---|---|
| `CIRCUIT` | `"Circuit2"` | Circuit identifier used in MQTT topics |
| `SENSORS["HighSide"]` | `0x5A` | I2C address of the high-side MLX90614 |
| `SENSORS["LowSide"]` | `0x5B` | I2C address of the low-side MLX90614 |
| `SENSORS["Evaporator"]` | `0x5C` | I2C address of the evaporator MLX90614 |
| `SENSORS["EXV"]` | `0x5D` | I2C address of the EXV MLX90614 |

Circuit2 uses a TEV (thermostatic expansion valve), which self-regulates superheat across the full speed range — no `VFD_MAX_RPM` ceiling is needed.

See `final/README.md` for full configuration reference, MQTT topics, data storage format, and startup sequence details.
