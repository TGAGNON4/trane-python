## Files
- `main.py` — entrypoint and `Application` orchestrator: setup, sample loop, shared mutators (`apply_setpoint`, `request_shutdown`, `request_start`, `set_unit`)
- `mqtt_handlers.py` — MQTT `on_connect` / `on_message` callbacks, bound to the `Application` via `attach()`
- `mqtt_publish.py` — live publish helpers: per-sample sensor topics, retained saturation table, download payloads, and the canonical `data_topic()` naming helper
- `mqtt_replay.py` — historical replay: `publish_time_row` / `publish_time_range` push stored samples back onto the live topics
- `compressor.py` — `Compressor` class: PID factory, PWM/VFD output, startup ramp, RPM override, status lines
- `hmi.py` — `HMIController`: Nextion serial reader, `pump()` callback drain, `request_display_setpoint()` outbound queue
- `sensors.py` — I2C bus setup, MLX90614 temperature reads, and ADS1115 pressure reads
- `storage.py` — file parsing, buffered persistence, and date/time range helpers
- `models.py` — `TempRow` and `PressureRow` dataclasses used across modules
- `coolprop_props.py` — R-1234yf thermodynamic property helpers using CoolProp (saturation table, state points)
- `config.py` — all tunable settings and constants (circuit ID, PID gains, PWM, HMI, MQTT broker)
- `simulate_startup.py` — offline simulation of the compressor startup ramp; exports CSV or `.xlsx`

### Module dependency overview

```
main.py ──┬── compressor.py
           ├── hmi.py
           ├── sensors.py
           ├── storage.py
           ├── mqtt_publish.py ── coolprop_props.py
           └── mqtt_handlers.py ──┬── mqtt_publish.py
                                  └── mqtt_replay.py ── mqtt_publish.py
```

`Application` is the only module holding cross-cutting state. Handlers and the
HMI never reach back into each other directly — both go through the
`Application` instance, so the data flow stays one-way.

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
| `SPACE_SETPOINT` | `22.2` | Initial temperature setpoint (°C, ~72°F) |
| `SAMPLE_SECONDS` | `1.0` | Sensor read interval |
| `PID_KP / KI / KD` | `0.3 / 0.02 / 0.0` | PID gains (PI by default) |
| `PID_SAMPLE_TIME` | `1.0` | Seconds between PID updates |
| `PID_ON_THRESHOLD` | `0.1` | Minimum PID output before compressor turns on |
| `VFD_MIN_RPM / VFD_MAX_RPM` | `2200 / 4500` | Compressor operating range |
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
| `<circuit>/Status` | `Starting` / `Running` / `Shutting Down` | Live circuit operating state (retained, published every sample) |
| `<circuit>/Unit` | `C` / `F` | Active display unit reported by the Pi (retained) |
| `Data/<circuit>/Setpoint_Record` | `<value>` or `<circuit> <value>` | Update temperature setpoint |
| `Data/<circuit>/Compressor_Shutdown` | any | Ramp compressor down to `VFD_MIN_RPM` and hold |
| `Data/<circuit>/Compressor_Shutdown_Status` | `ramping_to_min` | Published when shutdown is triggered (retained) |
| `Data/<circuit>/Compressor_Start` | any | Restart the compressor startup sequence (recovers from shutdown) |
| `Data/<circuit>/Unit_Change` | `C` / `F` | Request a unit change from the website; Pi updates HMI and re-publishes `<circuit>/Unit` |

### Data requests
| Request topic | Response topic | Description |
|---|---|---|
| `Data/<circuit>/Available_Dates_Request` | `Data/<circuit>/Available_Dates` | Comma-separated list of dates with logged data |
| `Data/<circuit>/Available_Time_Ranges_Request` | `Data/<circuit>/Available_Time_Ranges` | First and last timestamp for a given date |
| `Data/<circuit>/Temperature_Download_Request` | `Data/<circuit>/Temperature_Download` | Full day temperature file |
| `Data/<circuit>/Pressure_Download_Request` | `Data/<circuit>/Pressure_Download` | Full day pressure file |
| `Data/<circuit>/Setpoint_Download_Request` | `Data/<circuit>/Setpoint_Download` | Full day setpoint change log |
| `Data/<circuit>/Select_Time_Request` | — | Replays a single historical sample back to sensor topics |
| `Data/<circuit>/Select_Range_Request` | — | Replays a time range back to sensor topics |

## Compressor Startup Sequence
On startup, the compressor runs a controlled ramp-and-hold sequence before PID control begins:
1. RPM ramps from 0 to `STARTUP_RPM` at a rate not exceeding `MAX_RPM_PER_SEC` (Max is 120 RPM/s, but 60 RPM/s was smoother)
2. RPM is held at `STARTUP_RPM` for the remainder of `STARTUP_DURATION_SECONDS`
3. PID control takes over after the hold completes

RPM is mapped to PWM frequency for the VFD:
- 0–`VFD_RPM_AT_MIN_SPEED` → 0–`VFD_FREQ_AT_MIN_SPEED` Hz (pre-start warmup range)
- `VFD_RPM_AT_MIN_SPEED`–`VFD_RPM_AT_MAX_SPEED` → `VFD_FREQ_AT_MIN_SPEED`–`VFD_FREQ_AT_MAX_SPEED` Hz (full VFD scale)
- Below `VFD_MIN_RPM` during PID control → `VFD_FREQ_OFF` Hz (compressor off)

## RPM Override
The compressor RPM can be overridden from the website at any time after startup completes.

**Via MQTT:** publish a numeric value to `<circuit>/Compressor_RPM` (e.g. `3200`). Publish an empty payload to clear. Values are clamped to `[VFD_MIN_RPM, VFD_MAX_RPM]`.

While an override is active the PID loop is bypassed. RPM changes are rate-limited to `MAX_RPM_PER_SEC`. A setpoint change from either the dashboard or HMI will automatically clear any active override.

## Circuit Status
The Pi publishes one of four states to `<circuit>/Status` (retained, refreshed every sample):
- **Off** — boot/idle state; PWM is held at `VFD_FREQ_OFF` until the operator presses the start button (HMI `b_startup` or website / MQTT `Compressor_Start`)
- **Starting** — startup ramp/hold is running (cannot be interrupted by an override)
- **Running** — startup complete; PID is in control (or a non-shutdown override is active)
- **Shutting Down** — `Compressor_Shutdown` was requested; compressor is held at `VFD_MIN_RPM`

The compressor does **not** start automatically on boot — `init_pwm()` initializes the PWM hardware and waits for an explicit start command. An "Off" or "Shutting Down" circuit is brought online by publishing to `Data/<circuit>/Compressor_Start` (or pressing `b_startup` on the HMI), which clears the idle/shutdown flag, drops any active override, and runs the startup sequence.

## HMI (Nextion)
The HMI is a Nextion serial touch panel wired to the Pi's UART (`HMI_PORT` in `config.py`, default `/dev/ttyS0` at 9600 baud). Each panel "page" sends a short ASCII or binary command on touch events; [`hmi.py`](hmi.py) parses these on a daemon serial thread and pushes them onto private queues. The `Application` drains them once per sample tick by calling `hmi.pump(on_setpoint=…, on_shutdown=…, on_startup=…, on_unit=…)`. Outbound display updates from the website use `hmi.request_display_setpoint(value)`, which the next `pump()` flushes to the panel.

For panel design (component naming, event scripting, flashing the panel over USB-TTL or microSD, etc.), see Nextion's documentation:
- Official site: https://nextion.tech/
- Editor download + manuals: https://nextion.tech/nextion-editor/
- Instruction set reference: https://nextion.tech/instruction-set/

### Panel layout
The panel exposes one number control and four buttons. The +/- buttons increment and decrement the setpoint locally on the panel and re-emit it as a setpoint event — the Pi sees a single uniform setpoint code path.

| Component | Type | Purpose |
|---|---|---|
| `n_setpoint` | Number | Current setpoint, displayed in the active unit |
| `b_plus` | Button | Increment setpoint by 1 (in display unit) |
| `b_minus` | Button | Decrement setpoint by 1 (in display unit) |
| `b_unit` | Button | Toggle °C / °F display |
| `b_startup` | Button | Restart the compressor startup sequence |
| `b_shutdown` | Button | Ramp compressor down to `VFD_MIN_RPM` and hold |

### Commands the Pi expects
Every command terminates with the Nextion's standard `0xFF 0xFF 0xFF` packet boundary.

| Wire format | From the panel | Action on the Pi |
|---|---|---|
| `0x71 <int32_le>` | Setpoint changed (typed value, `b_plus`, or `b_minus` issuing `get n_setpoint.val`) | Convert from the panel's current display unit, clamp to `HMI_SETPOINT_MIN_C / MAX_C`, push to the control loop |
| `U` | `b_unit` released | Switch panel between °C / °F, re-publish `<circuit>/Unit`, sync website |
| `S` | `b_shutdown` released | Ramp to `VFD_MIN_RPM` and hold; status → `Shutting Down` |
| `T` | `b_startup` released | Re-run the startup ramp; status → `Starting` (recovers from shutdown) |

### Commands the Pi sends back
| Component | Command issued | Used for |
|---|---|---|
| `HMI_COMPONENT_SETPOINT` (`n_setpoint`) | `<comp>.val=<int>` and `<comp>.minval=` / `<comp>.maxval=` | Push current setpoint and update the input bounds when the unit changes |
| `HMI_COMPONENT_UNIT` (`b_unit`) | `<comp>.txt="°C"` / `"°F"` | Display the active unit label on the toggle button |

### Nextion event code
The Nextion `print` instruction emits raw bytes with **no** terminator, so each event also issues `printh ff ff ff` to close the packet that `hmi.py` is splitting on. Rename the components in the Editor (or update the `HMI_COMPONENT_*` constants in `config.py`) if you use different names.

**`b_unit`** — Touch Release Event:
```nextion
print "U"
printh ff ff ff
```

**`b_shutdown`** — Touch Release Event:
```nextion
print "S"
printh ff ff ff
```

**`b_startup`** — Touch Release Event:
```nextion
print "T"
printh ff ff ff
```

**`n_setpoint`** — Touch Release Event of the number itself or of the keypad's "OK" button. `get` causes the Nextion to send back `0x71 <int32_le> 0xFF 0xFF 0xFF`, which `hmi.py` parses as the new setpoint:
```nextion
get n_setpoint.val
```

**`b_plus`** — Touch Release Event. Bump the local value by 1, then emit it through the same setpoint code path. `hmi.py` clamps to `HMI_SETPOINT_MIN_C` / `MAX_C`, so no on-panel bounds check is needed:
```nextion
n_setpoint.val=n_setpoint.val+1
get n_setpoint.val
```

**`b_minus`** — Touch Release Event. Mirror image of `b_plus`:
```nextion
n_setpoint.val=n_setpoint.val-1
get n_setpoint.val
```

**Page Initialization** — request the current setpoint from the Pi when the page loads so the panel reflects the live value (the Pi also pushes the setpoint at startup, but this guards against the panel rebooting mid-session):
```nextion
get n_setpoint.val
```

## Unit Sync (HMI ↔ Website)
Both interfaces stay in lockstep:
- HMI `U` press → Pi publishes new unit on `<circuit>/Unit` → website updates its display.
- Website unit toggle → publishes `Data/<circuit>/Unit_Change` → Pi updates the HMI panel and re-publishes `<circuit>/Unit`.

The Pi always stores setpoints internally in °C; the HMI and website each convert at their own boundary.

## Simulating the Startup Profile
```bash
python3 final/simulate_startup.py --target 3200 --step 0.5 --out startup_3200.xlsx
```
Outputs a time series of `(time_s, desired_rpm, pwm_freq_hz)`. Writes `.xlsx` if `openpyxl` is available, otherwise falls back to CSV. Omit `--out` to print to stdout.

## MQTT Broker (EC2 + Mosquitto + Let's Encrypt)
The Pis and the website all talk to a Mosquitto broker hosted on a small AWS EC2 instance reachable as `seniordesignmqtt.duckdns.org`. The broker exposes:
- `1883/tcp` — plain MQTT (used by the Pis on the LAN)
- `8083/tcp` — MQTT-over-WebSockets with TLS (used by the website's `mqtt.connect("wss://…:8083")`)

The TLS certificate is issued by Let's Encrypt via certbot's DNS/HTTP challenge and renewed automatically.

### One-time setup

**1. EC2 instance.** Launch an Ubuntu LTS `t3.micro` (or larger). In the security group open inbound `22` (admin), `80` (certbot HTTP-01 challenge), `1883`, and `8083`. Allocate an Elastic IP so the public IP is stable across stop/start.

**2. DuckDNS.** Create the `seniordesignmqtt` subdomain on https://www.duckdns.org/ and point it at the EC2 Elastic IP. Add a cron job on the EC2 instance so the record stays current even if the IP ever changes:
```bash
mkdir -p ~/duckdns && cd ~/duckdns
cat > duck.sh <<'EOF'
echo url="https://www.duckdns.org/update?domains=seniordesignmqtt&token=<DUCKDNS_TOKEN>&ip=" \
  | curl -k -o ~/duckdns/duck.log -K -
EOF
chmod 700 duck.sh
( crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1" ) | crontab -
```

**3. Install Mosquitto and certbot.**
```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients certbot
```

**4. Get the certificate.** Stop Mosquitto so port 80 is free for the HTTP-01 challenge, then run certbot in standalone mode:
```bash
sudo systemctl stop mosquitto
sudo certbot certonly --standalone -d seniordesignmqtt.duckdns.org \
  --agree-tos -m <your-email> --non-interactive
```
This writes the cert chain to `/etc/letsencrypt/live/seniordesignmqtt.duckdns.org/`.

**5. Configure Mosquitto.** Mosquitto runs as the `mosquitto` user, which can't read `/etc/letsencrypt/live` directly. Use a renewal hook (step 6) to copy the certs into a directory it can read. Create `/etc/mosquitto/conf.d/seniordesign.conf`:
```conf
# Plain MQTT for LAN clients (the Pis)
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd

# Secure WebSockets for the website
listener 8083
protocol websockets
cafile   /etc/mosquitto/certs/chain.pem
certfile /etc/mosquitto/certs/fullchain.pem
keyfile  /etc/mosquitto/certs/privkey.pem
```
Create the user that the Pis and website authenticate as (matches `USERNAME` / `PASSWORD` in `config.py`):
```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd dev
# enter the same password you set in config.py
```

**6. Auto-renewal hook.** Drop this script at `/etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh` so every successful renewal copies fresh certs into Mosquitto's directory and reloads it:
```bash
#!/bin/bash
set -e
LIVE=/etc/letsencrypt/live/seniordesignmqtt.duckdns.org
DEST=/etc/mosquitto/certs
install -d -o mosquitto -g mosquitto -m 0750 "$DEST"
install -o mosquitto -g mosquitto -m 0640 "$LIVE/fullchain.pem" "$DEST/fullchain.pem"
install -o mosquitto -g mosquitto -m 0640 "$LIVE/privkey.pem"   "$DEST/privkey.pem"
install -o mosquitto -g mosquitto -m 0640 "$LIVE/chain.pem"     "$DEST/chain.pem"
systemctl reload mosquitto
```
```bash
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh   # run once now to seed the certs
sudo systemctl enable --now mosquitto
```

The Ubuntu `certbot` package ships a `certbot.timer` systemd unit that runs `certbot renew` twice a day; renewals only happen inside the last 30 days of the cert's life and trigger the deploy hook above. Confirm the timer is active:
```bash
systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

### Verifying the broker
```bash
# From any machine on the internet:
mosquitto_sub -h seniordesignmqtt.duckdns.org -p 1883 \
  -u dev -P '<password>' -t 'Circuit1/#' -v

# WebSocket TLS endpoint (used by the website):
openssl s_client -connect seniordesignmqtt.duckdns.org:8083 -servername seniordesignmqtt.duckdns.org </dev/null
```

## Notes
- To change behaviour, update the relevant module and keep all constants in `config.py`.
- `RUN_FOR_SECONDS` in `config.py` is `None` by default (run forever). Set to a number of seconds to stop automatically.
