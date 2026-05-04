"""Project-wide tunables. Per-circuit overrides live in circuit{1,2}/config.py."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out

_env = _load_env()

# -----------------
# CONFIGURATION - Edit these values for each Pi
# -----------------
CIRCUIT = "Circuit1"  # Change to "Circuit2" for the second Pi
BASE_DIR = Path("/home/team6/data")
SPACE_SETPOINT = 22.2  # Initial temperature setpoint (~72°F)
SAMPLE_SECONDS = 1.0  # How often to read sensors
# RAM buffering (reduce SD card writes)
RAM_BUFFER_MAX_BYTES = 1024 * 1024  # Flush when buffered bytes exceed this
RAM_BUFFER_MAX_SECONDS = 60.0      # Flush at least this often
STORAGE_PURGE_THRESHOLD = 0.90     # Delete oldest data when disk usage exceeds this
STORAGE_PURGE_CHECK_INTERVAL = 120  # Check disk usage every N samples
# Runtime limit (seconds). Set to None to run forever.
RUN_FOR_SECONDS: float | None = None
#RUN_FOR_SECONDS = 300

# HMI (Nextion) serial settings
HMI_ENABLED = True
HMI_PORT = "/dev/ttyS0"
HMI_BAUD = 9600
HMI_SETPOINT_MIN_C = 18.9  # ~66°F, practical minimum for this system
HMI_SETPOINT_MAX_C = 32.0
HMI_COMPONENT_SETPOINT = "n_setpoint"
HMI_COMPONENT_UNIT = "b_unit"
HMI_PAGE_STATUS = 0           # Nextion page number for status messages (power-on default)
HMI_PAGE_NORMAL = 1           # Nextion page number for normal operation
HMI_COMPONENT_STATUS_TEXT = "t_status"  # Text component on status page

# PID control (PI) settings
PID_ENABLED = True  # Enable PID control
PID_KP = 0.3
PID_KI = 0.02
PID_KD = 0.0
PID_SAMPLE_TIME = 2.0
PID_OUTPUT_LIMITS = (0.0, 1.0)  # Normalized output (0-1)

# PWM control (compressor) settings
PWM_ENABLED = True  # Enable PWM control
PWM_PIN = 12  # GPIO12 / PWM0
PWM_FREQUENCY_HZ = 1000  # PWM carrier frequency in Hz
PWM_DUTY_CYCLE = 50  # Duty cycle % sent to VFD signal input
VFD_MIN_RPM = 2200.0        # PWM floor: keeps PID and UI above the range where Pi PWM inaccuracy could stall the VFD
VFD_SHUTDOWN_RPM = 1900.0   # RPM target when graceful shutdown is requested
VFD_MAX_RPM = 4500.0        # Software maximum operating RPM
VFD_FREQ_OFF = 300          # PWM frequency (Hz) sent to VFD when compressor is off
# VFD hardware speed scale: 1000–10000 Hz maps linearly to 2000–6000 RPM
VFD_FREQ_AT_MIN_SPEED = 1000   # Hz at VFD_RPM_AT_MIN_SPEED
VFD_RPM_AT_MIN_SPEED = 2000    # RPM at VFD_FREQ_AT_MIN_SPEED
VFD_FREQ_AT_MAX_SPEED = 10000  # Hz at VFD_RPM_AT_MAX_SPEED
VFD_RPM_AT_MAX_SPEED = 6000    # RPM at VFD_FREQ_AT_MAX_SPEED
MAX_RPM_PER_SEC = 60.0  # Maximum RPM ramp rate during startup and override
PID_ON_THRESHOLD = 0.1  # Minimum PID output before compressor turns on
PWM_STATUS_INTERVAL = 2.5  # Seconds between PWM status prints

# Startup sequence settings
STARTUP_ENABLED = True  # Enable startup sequence
STARTUP_DURATION_SECONDS = 60  # Total startup duration (ramp + hold)
STARTUP_RPM = 2700  # RPM to ramp to and hold during startup

# MQTT Settings
BROKER = "seniordesignmqtt.duckdns.org"
PORT = 1883
USERNAME = os.environ.get("MQTT_USERNAME") or _env.get("MQTT_USERNAME") or "dev"
PASSWORD = os.environ.get("MQTT_PASSWORD") or _env.get("MQTT_PASSWORD") or ""

# Timezone
TZ = ZoneInfo("America/Chicago")

# Sensor I2C addresses — placeholders only. Each circuit overrides this in its
# own config.py with the addresses programmed into that Pi's MLX90614s.
SENSORS = {
    "HighSide": 0x01,
    "LowSide": 0x02,
    "Evaporator": 0x03,
    "EXV": 0x04,
}
ADS_ADDR = 0x48

# Pressure sensor (ratiometric 0.5–4.5 V, 0–300 PSI)
P_MAX_PSI = 300.0
P_V_MIN = 0.5   # sensor output at 0 PSI
P_V_MAX = 4.5   # sensor output at P_MAX_PSI
DISCONNECT_VOLTAGE = 0.1  # below this = sensor unpowered or disconnected (disconnected line is pulled to 0 V)
