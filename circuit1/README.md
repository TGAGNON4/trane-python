## Files
- `main.py` — orchestrates startup, MQTT, buffering, and the live control loop
- `config.py` — all tunable settings and constants (circuit ID, PID gains, PWM, HMI, MQTT broker)
- `models.py` — `TempRow` and `PressureRow` dataclasses used across modules
- `sensors.py` — I2C bus setup, MLX90614 temperature reads, and ADS1115 pressure reads
- `storage.py` — file parsing, buffered persistence, and date/time range helpers
- `mqtt_io.py` — MQTT publish helpers, download handlers, and CoolProp state point publishing
- `hmi.py` — Nextion HMI serial controller (setpoint display, unit toggle, RPM override)
- `control.py` — PID controller, PWM/VFD output, startup ramp sequence, and RPM override
- `coolprop_props.py` — R-1234yf thermodynamic property helpers using CoolProp (saturation table, state points)
- `simulate_startup.py` — offline simulation of the compressor startup ramp; exports CSV or `.xlsx`

## Run
```bash
python3 /final/main.py
```

## Dependencies
```bash
pip3 install paho-mqtt simple-pid adafruit-circuitpython-mlx90614 adafruit-circuitpython-ads1x15 CoolProp openpyxl RPi.GPIO --break-system-packages
```

## Configuration
All tunable values are in `config.py`. Key settings:

| Setting | Default | Description |
|---|---|---|
| `CIRCUIT` | `"Circuit1"` | Change to `"Circuit2"` for the second Pi |
| `SPACE_SETPOINT` | `5.0` | Initial temperature setpoint (°C) |
| `SAMPLE_SECONDS` | `1.0` | Sensor read interval |
| `PID_KP / KI / KD` | `0.3 / 0.02 / 0.0` | PID gains (PI by default) |
| `PID_SAMPLE_TIME` | `2.0` | Seconds between PID updates |
| `PID_ON_THRESHOLD` | `0.1` | Minimum PID output before compressor turns on |
| `VFD_MIN_RPM / VFD_MAX_RPM` | `2300 / 4600` | Compressor operating range |
| `VFD_FREQ_OFF` | `300` | PWM frequency (Hz) sent to VFD when compressor is off |
| `VFD_FREQ_AT_MIN_SPEED / VFD_RPM_AT_MIN_SPEED` | `1000 / 2000` | VFD hardware scale lower endpoint (Hz / RPM) |
| `VFD_FREQ_AT_MAX_SPEED / VFD_RPM_AT_MAX_SPEED` | `10000 / 6000` | VFD hardware scale upper endpoint (Hz / RPM) |
| `PWM_DUTY_CYCLE` | `50` | Duty cycle % sent to VFD signal input |
| `MAX_RPM_PER_SEC` | `60` | RPM ramp rate limit for startup and override |
| `STARTUP_RPM` | `2700` | RPM target during startup hold |
| `STARTUP_DURATION_SECONDS` | `60` | Total ramp + hold duration |
| `STORAGE_PURGE_THRESHOLD` | `0.90` | Delete oldest data when disk usage exceeds this |
| `STORAGE_PURGE_CHECK_INTERVAL` | `120` | Check disk usage every N samples |
| `HMI_ENABLED` | `True` | Enable/disable Nextion HMI |
| `RUN_FOR_SECONDS` | `None` | Set to a number to stop after N seconds |

## Data Storage
Sensor data is written to `BASE_DIR` (default `/home/team6/data`) in per-day folders:
```
/home/team6/data/
  DD-MM-YYYY/
    temps        # comma-separated temperature readings, one row per second
    pressures    # comma-separated pressure readings, one row per second
    setpoints    # timestamped setpoint change log
```

**temps** columns (CSV, 10 fields):
| Column | Field |
|---|---|
| 0 | Timestamp (`HH:MM:SS`) |
| 1 | HighSide ambient temperature (°C) |
| 2 | HighSide object temperature (°C) |
| 3 | LowSide ambient temperature (°C) |
| 4 | LowSide object temperature (°C) |
| 5 | Evaporator ambient temperature — discharge air / PID input (°C) |
| 6 | Evaporator object temperature (°C) |
| 7 | EXV ambient temperature (°C) |
| 8 | EXV object temperature (°C) |
| 9 | Space temperature — averaged ambient (°C) |

**pressures** columns (CSV, 5 fields):
| Column | Field |
|---|---|
| 0 | Timestamp (`HH:MM:SS`) |
| 1 | HighSide absolute pressure (Pa) |
| 2 | LowSide absolute pressure (Pa) |
| 3 | Evaporator absolute pressure (Pa) |
| 4 | EXV absolute pressure (Pa) |

**setpoints** format: `HH:MM:SS,<value_°C>` — one entry per setpoint change.
Writes are RAM-buffered and flushed to disk every 60 seconds or 1 MB, whichever comes first, to reduce SD card wear. `fsync` is called on every flush to protect against data loss on power failure.

## MQTT Topics
### Sensor data (published every sample, retained)
| Topic | Description |
|---|---|
| `<circuit>/HighSide_Temperature` | High side refrigerant temperature (°C) |
| `<circuit>/LowSide_Temperature` | Low side refrigerant temperature (°C) |
| `<circuit>/Evaporator_Temperature` | Evaporator refrigerant temperature (°C) |
| `<circuit>/EXV_Temperature` | EXV refrigerant temperature (°C) |
| `<circuit>/Discharge_Air_Temperature` | Discharge air temperature — PID control variable (°C) |
| `<circuit>/Space_Temperature` | Averaged ambient space temperature (°C) |
| `<circuit>/HighSide_AbsolutePressure` | High side pressure (Pa) |
| `<circuit>/LowSide_AbsolutePressure` | Low side pressure (Pa) |
| `<circuit>/Evaporator_AbsolutePressure` | Evaporator pressure (Pa) |
| `<circuit>/EXV_AbsolutePressure` | EXV pressure (Pa) |
| `<circuit>/Space_Setpoint_Temperature` | Active temperature setpoint (°C) |
| `<circuit>/Sample_Timestamp` | Sample time as epoch milliseconds |

### Thermodynamic data (requires CoolProp)
| Topic | Description |
|---|---|
| `Data/<circuit>/R1234yf_Saturation_Table` | Full R-1234yf saturation curve as JSON (retained, published once on startup) |
| `Data/<circuit>/R1234yf_State_Points` | Live cycle state point enthalpies and phases as JSON (not retained, published each sample) |

### Control
| Topic | Payload | Description |
|---|---|---|
| `<circuit>/Compressor_Current_RPM` | numeric | Current compressor RPM (published every sample, retained) |
| `<circuit>/Compressor_RPM` | numeric or empty | Set or clear RPM override (retained). Empty payload clears override. |
| `Data/<circuit>/Setpoint_Record` | `<value>` or `<circuit> <value>` | Update temperature setpoint |
| `Data/<circuit>/Compressor_Shutdown` | any | Ramp compressor down to `VFD_MIN_RPM` and hold |
| `Data/<circuit>/Compressor_Shutdown_Status` | `ramping_to_min` | Published when shutdown is triggered (retained) |

### Data requests
| Request topic | Response topic | Description |
|---|---|---|
| `Data/<circuit>/Available_Dates_Request` | `Data/<circuit>/Available_Dates` | Comma-separated list of dates with logged data |
| `Data/<circuit>/Available_Time_Ranges_Request` | `Data/<circuit>/Available_Time_Ranges` | First and last timestamp for a given date |
| `Data/<circuit>/Temperature_Download_Request` | `Data/<circuit>/Temperature_Download` | Full day temperature file |
| `Data/<circuit>/Pressure_Download_Request` | `Data/<circuit>/Pressure_Download` | Full day pressure file |
| `Data/<circuit>/Select_Range_Request` | — | Replays a time range back to sensor topics |

## Compressor Startup Sequence
On startup, the compressor runs a controlled ramp-and-hold sequence before PID control begins:
1. RPM ramps from 0 to `STARTUP_RPM` at a rate not exceeding `MAX_RPM_PER_SEC` (120 RPM/s)
2. RPM is held at `STARTUP_RPM` for the remainder of `STARTUP_DURATION_SECONDS`
3. PID control takes over after the hold completes

RPM is mapped to PWM frequency for the VFD:
- 0–`VFD_RPM_AT_MIN_SPEED` → 0–`VFD_FREQ_AT_MIN_SPEED` Hz (pre-start warmup range)
- `VFD_RPM_AT_MIN_SPEED`–`VFD_RPM_AT_MAX_SPEED` → `VFD_FREQ_AT_MIN_SPEED`–`VFD_FREQ_AT_MAX_SPEED` Hz (full VFD scale)
- Below `VFD_MIN_RPM` during PID control → `VFD_FREQ_OFF` Hz (compressor off)

## RPM Override
The compressor RPM can be overridden from the dashboard or HMI at any time after startup completes.

**Via MQTT:** publish a numeric value to `<circuit>/Compressor_RPM` (e.g. `3200`). Publish an empty payload to clear.

**Via HMI:** send `R<value>` from the Nextion (e.g. `R3200`). Send `R` with no value to clear.

While an override is active the PID loop is bypassed. RPM changes are rate-limited to `MAX_RPM_PER_SEC`. A setpoint change from either the dashboard or HMI will automatically clear any active override.

## Simulating the Startup Profile
```bash
python3 final/simulate_startup.py --target 3200 --step 0.5 --out startup_3200.xlsx
```
Outputs a time series of `(time_s, desired_rpm, pwm_freq_hz)`. Writes `.xlsx` if `openpyxl` is available, otherwise falls back to CSV. Omit `--out` to print to stdout.

## Notes
- To change behaviour, update the relevant module and keep all constants in `config.py`.
- `RUN_FOR_SECONDS` in `config.py` is `None` by default (run forever). Set to a number of seconds to stop automatically.
