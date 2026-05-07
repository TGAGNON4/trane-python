## Files
- `main.py` — shim: patches `sys.path` so this directory's `config.py` shadows `final/config.py`, then executes `final/main.py`
- `config.py` — Circuit1 overrides: inherits all defaults from `final/config.py` and overrides only circuit-specific values

## Run
```bash
python main.py
```

## Dependencies
```bash
pip3 install paho-mqtt simple-pid adafruit-circuitpython-mlx90614 adafruit-circuitpython-ads1x15 CoolProp openpyxl RPi.GPIO --break-system-packages
```

## Circuit1 Overrides
All base defaults live in `final/config.py`. This directory's `config.py` overrides only:

| Setting | Circuit1 Value | Description |
|---|---|---|
| `CIRCUIT` | `"Circuit1"` | Circuit identifier used in MQTT topics |
| `SENSORS["HighSide"]` | `0x1A` | I2C address of the high-side MLX90614 |
| `SENSORS["LowSide"]` | `0x4A` | I2C address of the low-side MLX90614 |
| `SENSORS["Evaporator"]` | `0x3A` | I2C address of the evaporator MLX90614 |
| `SENSORS["EXV"]` | `0x5A` | I2C address of the EXV MLX90614 |
| `VFD_MAX_RPM` | `4400.0` | Reduced from base 4500 — Circuit1 uses a capillary tube; above ~4400 RPM the cap tube overfeeds the evaporator |

See `final/README.md` for full configuration reference, MQTT topics, data storage format, and startup sequence details.
