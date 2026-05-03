import socket
import time
import threading
import queue
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

try:
    from simple_pid import PID
    PID_IMPORT_ERROR = None
except Exception as exc:
    PID = None
    PID_IMPORT_ERROR = exc

# -----------------
# CONFIGURATION - Edit these values for each Pi
# -----------------
CIRCUIT = "Circuit1"  # Change to "Circuit2" for the second Pi
BASE_DIR = Path("/home/team6/data")
SPACE_SETPOINT = 5.0  # Initial temperature setpoint
SAMPLE_SECONDS = 1.0  # How often to read sensors
# RAM buffering (reduce SD card writes)
RAM_BUFFER_MAX_BYTES = 256 * 1024  # Flush when buffered bytes exceed this
RAM_BUFFER_MAX_SECONDS = 15.0      # Flush at least this often
# Runtime limit (seconds). Set to None to run forever.
#RUN_FOR_SECONDS: float | None = None
RUN_FOR_SECONDS = 120

# HMI (Nextion) serial settings
HMI_ENABLED = True
HMI_PORT = "/dev/ttyS0"
HMI_BAUD = 9600
HMI_SETPOINT_MIN_C = -12.0
HMI_SETPOINT_MAX_C = 32.0
HMI_COMPONENT_SETPOINT = "n_setpoint"
HMI_COMPONENT_DISCHARGE = "n_discharge"
HMI_COMPONENT_UNIT = "b_unit"

# PID control (PI) settings
PID_ENABLED = False
PID_KP = 1.0
PID_KI = 0.1
PID_KD = 0.0
PID_SAMPLE_TIME = 1.0
PID_OUTPUT_LIMITS = (0.0, 1.0)  # Placeholder: 0-1 normalized output

# MQTT Settings
BROKER = "seniordesignmqtt.duckdns.org"
PORT = 1883
USERNAME = "dev"
PASSWORD = "trAneEseNdeS_4321"

# Timezone
TZ = ZoneInfo("America/Chicago")

# Sensor I2C addresses
SENSORS = {
    "HighSide": 0x1A,
    "LowSide": 0x4A,
    "Evaporator": 0x3A,
    "EXV": 0x5A,
}
ADS_ADDR = 0x48

# Pressure sensor (4-20 mA) config
R_SENSE_OHMS = 200.0
I_MIN_A = 0.004
I_MAX_A = 0.020
P_MAX_PSI = 150.0
DISCONNECT_VOLTAGE = 0.0035 * R_SENSE_OHMS  # below ~0.7V = no sensor

# -----------------
# Sensor imports
# -----------------
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    import adafruit_mlx90614
    SENSOR_IMPORT_ERROR = None
except Exception as exc:
    board = None
    busio = None
    ADS = None
    AnalogIn = None
    adafruit_mlx90614 = None
    SENSOR_IMPORT_ERROR = exc

try:
    import serial
    SERIAL_IMPORT_ERROR = None
except Exception as exc:
    serial = None
    SERIAL_IMPORT_ERROR = exc


@dataclass
class TempRow:
    timestamp: str
    high_ambient: float
    high_object: float
    low_ambient: float
    low_object: float
    evaporator_ambient: float
    evaporator_object: float
    exv_ambient: float  # Discharge air temperature
    exv_object: float   # EXV temperature
    space_temp: float


@dataclass
class PressureRow:
    timestamp: str
    high: float
    low: float
    evaporator: float
    exv: float


class HMIController:
    TERMINATOR = b"\xff\xff\xff"

    def __init__(self, port: str, baud: int, setpoint_queue: "queue.Queue[float]"):
        self._port = port
        self._baud = baud
        self._setpoint_queue = setpoint_queue
        self._lock = threading.Lock()
        self._ser: object | None = None
        self._reader: threading.Thread | None = None
        self._state = {
            "discharge_temp": 20.0,
            "setpoint_temp": 5.0,
            "unit": "C",
            "synced": False,
        }

    def start(self) -> bool:
        if serial is None:
            return False
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=0.1)
        except Exception:
            return False
        self._reader = threading.Thread(target=self._serial_read_loop, daemon=True)
        self._reader.start()
        time.sleep(1.0)
        self._nextion_send(f"get {HMI_COMPONENT_SETPOINT}.val")
        timeout = 3.0
        waited = 0.0
        while waited < timeout:
            with self._lock:
                if self._state["synced"]:
                    break
            time.sleep(0.1)
            waited += 0.1
        self._update_bounds_for_unit(self._state["unit"])
        self.update_display()
        return True

    def _nextion_send(self, cmd: str) -> None:
        if self._ser is None:
            return
        with self._lock:
            self._ser.write((cmd + "\xff\xff\xff").encode("iso-8859-1"))

    def _to_display(self, celsius: float, unit: str) -> int:
        if unit == "F":
            return round(celsius * 9 / 5 + 32)
        return round(celsius)

    def _from_display(self, value: int, unit: str) -> float:
        if unit == "F":
            return (value - 32) * 5 / 9
        return float(value)

    def _update_bounds_for_unit(self, unit: str) -> None:
        if unit == "F":
            sp_min = self._to_display(HMI_SETPOINT_MIN_C, "F")
            sp_max = self._to_display(HMI_SETPOINT_MAX_C, "F")
        else:
            sp_min, sp_max = int(HMI_SETPOINT_MIN_C), int(HMI_SETPOINT_MAX_C)
        self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.minval={sp_min}")
        self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.maxval={sp_max}")

    def update_display(self) -> None:
        with self._lock:
            unit = self._state["unit"]
            d = self._to_display(self._state["discharge_temp"], unit)
            s = self._to_display(self._state["setpoint_temp"], unit)
        unit_label = "°F" if unit == "F" else "°C"
        self._nextion_send(f"{HMI_COMPONENT_DISCHARGE}.val={d}")
        self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.val={s}")
        self._nextion_send(f'{HMI_COMPONENT_UNIT}.txt="{unit_label}"')

    def set_discharge_temp(self, celsius: float) -> None:
        with self._lock:
            self._state["discharge_temp"] = celsius
            unit = self._state["unit"]
            d = self._to_display(celsius, unit)
        self._nextion_send(f"{HMI_COMPONENT_DISCHARGE}.val={d}")

    def set_setpoint(self, celsius: float) -> None:
        with self._lock:
            self._state["setpoint_temp"] = max(HMI_SETPOINT_MIN_C, min(HMI_SETPOINT_MAX_C, float(celsius)))
        self.update_display()

    def _parse_hmi_event(self, data: bytes) -> None:
        if not data:
            return
        cmd = data[0]
        if cmd == 0x71 and len(data) == 5:
            raw_val = int.from_bytes(data[1:5], byteorder="little", signed=True)
            with self._lock:
                celsius = self._from_display(raw_val, self._state["unit"])
                celsius = max(HMI_SETPOINT_MIN_C, min(HMI_SETPOINT_MAX_C, celsius))
                self._state["setpoint_temp"] = celsius
                already_synced = self._state["synced"]
                self._state["synced"] = True
                unit = self._state["unit"]
            if already_synced:
                s = self._to_display(celsius, unit)
                self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.val={s}")
                self.update_display()
            try:
                self._setpoint_queue.put_nowait(celsius)
            except queue.Full:
                pass
        else:
            msg = data.replace(b"\xff", b"").decode("iso-8859-1", errors="ignore").strip()
            if msg == "U":
                with self._lock:
                    self._state["unit"] = "F" if self._state["unit"] == "C" else "C"
                    new_unit = self._state["unit"]
                    s = self._to_display(self._state["setpoint_temp"], new_unit)
                self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.val={s}")
                self._update_bounds_for_unit(new_unit)
                self.update_display()

    def _serial_read_loop(self) -> None:
        if self._ser is None:
            return
        buf = b""
        while True:
            chunk = self._ser.read(self._ser.in_waiting or 1)
            if not chunk:
                continue
            buf += chunk
            while self.TERMINATOR in buf:
                packet, buf = buf.split(self.TERMINATOR, 1)
                self._parse_hmi_event(packet)


# -----------------
# Helper functions
# -----------------

def data_topic(circuit: str, name: str) -> str:
    return f"Data/{circuit}/{name}"


def today_str() -> str:
    return datetime.now(TZ).strftime("%d-%m-%Y")


def dated_temps_file(base_dir: Path, date_str: str) -> Path:
    return base_dir / date_str / "temps"


def setpoints_file(base_dir: Path, date_str: str) -> Path:
    return base_dir / date_str / "setpoints"


def pressures_file(base_dir: Path, date_str: str) -> Path:
    return base_dir / date_str / "pressures"


def read_temps_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def append_temps_line(base_dir: Path, date_str: str, line: str) -> None:
    folder = base_dir / date_str
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "temps"
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_setpoint(base_dir: Path, date_str: str, value: float) -> None:
    folder = base_dir / date_str
    folder.mkdir(parents=True, exist_ok=True)
    path = setpoints_file(base_dir, date_str)
    now_time = datetime.now(TZ).strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{now_time},{value}\n")


def load_setpoints(base_dir: Path, date_str: str) -> list[tuple[str, float]]:
    path = setpoints_file(base_dir, date_str)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",", 1)
        if len(parts) != 2:
            continue
        try:
            rows.append((parts[0].strip(), float(parts[1].strip())))
        except ValueError:
            continue
    return rows


def parse_line(raw: str) -> TempRow:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 10:
        raise ValueError(f"expected 10 columns, got {len(parts)}")
    return TempRow(
        timestamp=parts[0],
        high_ambient=float(parts[1]),
        high_object=float(parts[2]),
        low_ambient=float(parts[3]),
        low_object=float(parts[4]),
        evaporator_ambient=float(parts[5]),
        evaporator_object=float(parts[6]),
        exv_ambient=float(parts[7]),  # Discharge air temperature
        exv_object=float(parts[8]),   # EXV temperature
        space_temp=float(parts[9]),
    )


def parse_pressure_line(raw: str) -> PressureRow:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 5:
        raise ValueError(f"expected 5 columns, got {len(parts)}")
    return PressureRow(
        timestamp=parts[0],
        high=float(parts[1]),
        low=float(parts[2]),
        evaporator=float(parts[3]),
        exv=float(parts[4]),
    )


def parse_requested_time(raw: str) -> tuple[str | None, str | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, None, None
    parts = raw.split()
    if len(parts) == 1:
        return None, None, parts[0].strip()
    if len(parts) == 2:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), None, parts[1].strip()
        return None, parts[0].strip(), parts[1].strip()
    if len(parts) >= 3:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), parts[1].strip(), parts[2].strip()
        return None, parts[0].strip(), parts[1].strip()
    return None, None, None


def parse_requested_range(raw: str) -> tuple[str | None, str | None, str | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, None, None, None
    parts = raw.split()
    if len(parts) == 2:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), None, parts[1].strip(), None
        return None, None, parts[0].strip(), parts[1].strip()
    if len(parts) == 3:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), None, parts[1].strip(), parts[2].strip()
        return None, parts[0].strip(), parts[1].strip(), parts[2].strip()
    if len(parts) >= 4:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
        return None, parts[0].strip(), parts[1].strip(), parts[2].strip()
    return None, None, None, None


def available_dates(base_dir: Path) -> list[str]:
    if not base_dir.exists():
        return []
    dates = []
    for item in sorted(base_dir.iterdir()):
        if item.is_dir() and (item / "temps").exists():
            dates.append(item.name)
    return dates


def time_range_for_date(base_dir: Path, date_str: str) -> str:
    temps_path = dated_temps_file(base_dir, date_str)
    if not temps_path.exists():
        return ""
    first = ""
    last = ""
    for line in temps_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ts = line.split(",", 1)[0].strip()
        if not first:
            first = ts
        last = ts
    if not first or not last:
        return ""
    return f"{first}-{last}"


def publish_available_dates(client: mqtt.Client, base_dir: Path, circuit: str) -> None:
    payload = ",".join(available_dates(base_dir))
    client.publish(data_topic(circuit, "Available_Dates"), payload, qos=0, retain=True)


def publish_time_ranges(client: mqtt.Client, base_dir: Path, date_str: str, circuit: str) -> None:
    payload = time_range_for_date(base_dir, date_str)
    client.publish(data_topic(circuit, "Available_Time_Ranges"), payload, qos=0, retain=False)


def parse_sample_epoch_ms(row_timestamp: str, active_path: Path) -> int:
    folder_name = active_path.parent.name
    try:
        sample_dt = datetime.strptime(f"{folder_name} {row_timestamp}", "%d-%m-%Y %H:%M:%S")
    except ValueError:
        now = datetime.now(TZ)
        sample_dt = datetime.strptime(
            f"{now.strftime('%d-%m-%Y')} {row_timestamp}",
            "%d-%m-%Y %H:%M:%S",
        )
    sample_dt = sample_dt.replace(tzinfo=TZ)
    return int(sample_dt.timestamp() * 1000)


def publish_row(
    client: mqtt.Client,
    circuit: str,
    row: TempRow,
    space_setpoint: float,
    sample_epoch_ms: int,
    pressure: PressureRow | None = None,
) -> None:
    topic_values = {
        f"{circuit}/HighSide_Temperature": row.high_object,
        f"{circuit}/EXV_Temperature": row.exv_object,
        f"{circuit}/LowSide_Temperature": row.low_object,
        f"{circuit}/Evaporator_Temperature": row.evaporator_object,
        f"{circuit}/Space_Temperature": row.space_temp,
        f"{circuit}/Space_Setpoint_Temperature": space_setpoint,
        f"{circuit}/Sample_Timestamp": sample_epoch_ms,
        f"{circuit}/Discharge_Air_Temperature": row.exv_ambient,
    }
    for topic, value in topic_values.items():
        client.publish(topic, f"{value}", qos=0, retain=True)
    if pressure:
        client.publish(f"{circuit}/HighSide_AbsolutePressure", f"{pressure.high}", qos=0, retain=True)
        client.publish(f"{circuit}/LowSide_AbsolutePressure", f"{pressure.low}", qos=0, retain=True)
        client.publish(f"{circuit}/Evaporator_AbsolutePressure", f"{pressure.evaporator}", qos=0, retain=True)
        client.publish(f"{circuit}/EXV_AbsolutePressure", f"{pressure.exv}", qos=0, retain=True)


def load_pressures(base_dir: Path, date_str: str) -> dict[str, PressureRow]:
    path = pressures_file(base_dir, date_str)
    if not path.exists():
        return {}
    rows: dict[str, PressureRow] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = parse_pressure_line(line)
        except ValueError:
            continue
        rows[row.timestamp] = row
    return rows


def publish_time_row(
    client: mqtt.Client,
    base_dir: Path,
    circuit: str,
    space_setpoint: float,
    raw_request: str,
) -> None:
    circuit_name, date_str, time_str = parse_requested_time(raw_request)
    if not time_str:
        client.publish(data_topic(circuit, "Select_Time_Status"), "missing time", qos=0, retain=False)
        return
    if not date_str:
        date_str = today_str()
    if not circuit_name:
        circuit_name = circuit

    temps_path = dated_temps_file(base_dir, date_str)
    if not temps_path.exists():
        client.publish(data_topic(circuit, "Select_Time_Status"), "date not found", qos=0, retain=False)
        return

    setpoints = load_setpoints(base_dir, date_str)
    pressures = load_pressures(base_dir, date_str)
    setpoint_value = space_setpoint
    for ts, val in setpoints:
        if ts <= time_str:
            setpoint_value = val
        else:
            break

    for line in temps_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = parse_line(line)
        except ValueError:
            continue
        if row.timestamp == time_str:
            pressure = pressures.get(row.timestamp)
            publish_row(
                client,
                circuit_name,
                row,
                setpoint_value,
                parse_sample_epoch_ms(row.timestamp, temps_path),
                pressure,
            )
            client.publish(data_topic(circuit, "Select_Time_Status"), "ok", qos=0, retain=False)
            return

    client.publish(data_topic(circuit, "Select_Time_Status"), "time not found", qos=0, retain=False)


def publish_time_range(
    client: mqtt.Client,
    base_dir: Path,
    circuit: str,
    space_setpoint: float,
    raw_request: str,
) -> None:
    circuit_name, date_str, start_time, end_time = parse_requested_range(raw_request)
    if not start_time or not end_time:
        client.publish(data_topic(circuit, "Select_Range_Status"), "missing range", qos=0, retain=False)
        return
    if not date_str:
        date_str = today_str()
    if not circuit_name:
        circuit_name = circuit

    temps_path = dated_temps_file(base_dir, date_str)
    if not temps_path.exists():
        client.publish(data_topic(circuit, "Select_Range_Status"), "date not found", qos=0, retain=False)
        return

    setpoints = load_setpoints(base_dir, date_str)
    pressures = load_pressures(base_dir, date_str)
    setpoint_idx = 0
    current_setpoint = space_setpoint

    published = False
    for line in temps_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = parse_line(line)
        except ValueError:
            continue
        while setpoint_idx < len(setpoints) and setpoints[setpoint_idx][0] <= row.timestamp:
            current_setpoint = setpoints[setpoint_idx][1]
            setpoint_idx += 1
        if start_time <= row.timestamp <= end_time:
            pressure = pressures.get(row.timestamp)
            publish_row(
                client,
                circuit_name,
                row,
                current_setpoint,
                parse_sample_epoch_ms(row.timestamp, temps_path),
                pressure,
            )
            published = True

    client.publish(data_topic(circuit, "Select_Range_Status"), "ok" if published else "time not found", qos=0, retain=False)


# -----------------
# Sensor I2C setup
# -----------------

def init_i2c_devices():
    if board is None or busio is None or ADS is None or AnalogIn is None or adafruit_mlx90614 is None:
        return None
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c, address=ADS_ADDR)
    ads.gain = 1
    a0 = AnalogIn(ads, 0)
    a1 = AnalogIn(ads, 1)
    a2 = AnalogIn(ads, 2)
    a3 = AnalogIn(ads, 3)
    mlxs = []
    for addr in SENSORS.values():
        try:
            mlxs.append(adafruit_mlx90614.MLX90614(i2c, address=addr))
        except Exception:
            mlxs.append(None)
    return mlxs, a0, a1, a2, a3


def read_mlx_temp_c(mlx) -> tuple[float, float]:
    return mlx.ambient_temperature, mlx.object_temperature


def voltage_to_pa(volts: float) -> float:
    if volts != volts:  # NaN check
        return float("nan")
    current = volts / R_SENSE_OHMS
    if current < I_MIN_A:
        current = I_MIN_A
    if current > I_MAX_A:
        current = I_MAX_A
    psi = ((current - I_MIN_A) / (I_MAX_A - I_MIN_A)) * P_MAX_PSI
    return psi * 6894.757


def pressure_connected(volts: float) -> bool:
    return volts > DISCONNECT_VOLTAGE


def safe_read_temp_c(mlx) -> tuple[float, float]:
    try:
        if mlx is None:
            return float("nan"), float("nan")
        return read_mlx_temp_c(mlx)
    except Exception:
        return float("nan"), float("nan")


def safe_read_pressure_pa(channel) -> float:
    try:
        volts = channel.voltage
        if not pressure_connected(volts):
            return float("nan")
        return voltage_to_pa(volts)
    except Exception:
        return float("nan")


def init_pid() -> PID | None:
    if PID is None:
        return None
    pid = PID(Kp=PID_KP, Ki=PID_KI, Kd=PID_KD, setpoint=SPACE_SETPOINT)
    pid.sample_time = PID_SAMPLE_TIME
    pid.output_limits = PID_OUTPUT_LIMITS
    return pid


def apply_control_output(output: float) -> None:
    """
    TODO: Implement actuator control.
    - If output is normalized, map to voltage/RPM/command here.
    - Example: set_pwm_duty_cycle(output) or publish to a motor controller.
    """
    pass


def read_live_line(mlxs, a0, a1, a2, a3) -> tuple[str, str, PressureRow, str]:
    now = datetime.now(TZ)
    current_time = now.strftime("%H:%M:%S")
    date_str = now.strftime("%d-%m-%Y")
    high_amb, high_obj = safe_read_temp_c(mlxs[0])
    low_amb, low_obj = safe_read_temp_c(mlxs[1])
    evap_amb, evap_obj = safe_read_temp_c(mlxs[2])
    exv_amb, exv_obj = safe_read_temp_c(mlxs[3])  # EXV sensor: ambient=discharge air, object=EXV temp

    high_p = safe_read_pressure_pa(a0)
    low_p = safe_read_pressure_pa(a2)
    evap_p = safe_read_pressure_pa(a3)
    exv_p = safe_read_pressure_pa(a1)

    space_temp = (high_amb + low_amb + evap_amb + exv_amb) / 4
    space_temp = (high_amb + low_amb + evap_amb + exv_amb) / 4

    temp_readings = [
        current_time,
        high_amb, high_obj,
        low_amb, low_obj,
        evap_amb, evap_obj,
        exv_amb, exv_obj,  # Discharge air temp (ambient), EXV temp (object)
        space_temp,
    ]
    temp_line = ",".join(str(v) for v in temp_readings)

    pressure_readings = [current_time, high_p, low_p, evap_p, exv_p]
    pressure_line = ",".join(str(v) for v in pressure_readings)

    pressure_row = PressureRow(
        timestamp=current_time,
        high=high_p,
        low=low_p,
        evaporator=evap_p,
        exv=exv_p,
    )

    return temp_line, pressure_line, pressure_row, date_str


def append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())


def flush_buffers(base_dir: Path, date_str: str, temps: list[str], pressures: list[str]) -> None:
    append_lines(dated_temps_file(base_dir, date_str), temps)
    append_lines(pressures_file(base_dir, date_str), pressures)


# -----------------
# Main
# -----------------

def main() -> None:
    print(f"Starting data server for {CIRCUIT}")
    print(f"Base directory: {BASE_DIR}")
    print(f"MQTT broker: {BROKER}:{PORT}")
    print(f"Sample rate: {SAMPLE_SECONDS}s")
    
    # Create base directory if needed
    if not BASE_DIR.exists():
        try:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            print(f"Created base dir: {BASE_DIR}")
        except OSError as exc:
            print(f"Base dir not found and could not be created: {BASE_DIR} ({exc})")

    # Initialize I2C devices
    i2c_devices = init_i2c_devices()
    if i2c_devices is None:
        if SENSOR_IMPORT_ERROR:
            print(f"Sensor import error: {SENSOR_IMPORT_ERROR}")
        else:
            print("Live mode needs board/busio + adafruit_mlx90614 + adafruit_ads1x15.")
            print("Also verify I2C is enabled and sensors are visible on the bus.")
        return

    current_setpoint = {CIRCUIT: SPACE_SETPOINT}
    pid = init_pid()

    hmi_setpoint_queue: queue.Queue[float] = queue.Queue(maxsize=5)
    hmi: HMIController | None = None
    if HMI_ENABLED:
        if serial is None:
            print(f"HMI serial unavailable: {SERIAL_IMPORT_ERROR}")
        else:
            hmi = HMIController(HMI_PORT, HMI_BAUD, hmi_setpoint_queue)
            if hmi.start():
                hmi.set_setpoint(current_setpoint[CIRCUIT])
                print(f"HMI connected on {HMI_PORT}")
            else:
                hmi = None
                print(f"HMI failed to open {HMI_PORT}")

    # Setup MQTT client
    client = mqtt.Client(
        protocol=mqtt.MQTTv5,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(USERNAME, PASSWORD)

    def apply_setpoint(value: float, publish_mqtt: bool, update_hmi: bool) -> None:
        """Handle a new setpoint value: clamp, persist, update state, HMI, PID, and optionally publish via MQTT.

        This is the central handler for setpoint updates originating from
        either the HMI or incoming MQTT UI messages.
        """
        clamped = max(HMI_SETPOINT_MIN_C, min(HMI_SETPOINT_MAX_C, float(value)))
        print(f"apply_setpoint called: value={value} clamped={clamped} publish_mqtt={publish_mqtt} update_hmi={update_hmi}")
        append_setpoint(BASE_DIR, today_str(), clamped)
        current_setpoint[CIRCUIT] = clamped
        if pid is not None:
            pid.setpoint = clamped
        if update_hmi and hmi:
            hmi.set_setpoint(clamped)
        if publish_mqtt:
            client.publish(
                f"{CIRCUIT}/Space_Setpoint_Temperature",
                f"{clamped}",
                qos=0,
                retain=True,
            )

    def on_connect(_client, _userdata, _flags, _reason_code, _properties):
        print(f"Connected to MQTT broker")
        _client.subscribe(data_topic(CIRCUIT, "Available_Dates_Request"))
        _client.subscribe(data_topic(CIRCUIT, "Available_Time_Ranges_Request"))
        _client.subscribe(data_topic(CIRCUIT, "Download_Request"))
        _client.subscribe(data_topic(CIRCUIT, "Pressure_Download_Request"))
        _client.subscribe(data_topic(CIRCUIT, "Select_Time_Request"))
        _client.subscribe(data_topic(CIRCUIT, "Select_Range_Request"))
        _client.subscribe(data_topic(CIRCUIT, "Setpoint_Record"))
        publish_available_dates(_client, BASE_DIR, CIRCUIT)
        publish_time_ranges(_client, BASE_DIR, today_str(), CIRCUIT)

    def on_message(_client, _userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip()
        if msg.topic == data_topic(CIRCUIT, "Available_Dates_Request"):
            publish_available_dates(_client, BASE_DIR, CIRCUIT)
        elif msg.topic == data_topic(CIRCUIT, "Available_Time_Ranges_Request"):
            date_str = payload or today_str()
            publish_time_ranges(_client, BASE_DIR, date_str, CIRCUIT)
        elif msg.topic == data_topic(CIRCUIT, "Download_Request"):
            date_str = payload or today_str()
            temps_path = dated_temps_file(BASE_DIR, date_str)
            body = read_temps_text(temps_path)
            _client.publish(
                data_topic(CIRCUIT, "Download"),
                f"DATE:{date_str}\n{body}",
                qos=0,
                retain=False,
            )
        elif msg.topic == data_topic(CIRCUIT, "Pressure_Download_Request"):
            date_str = payload or today_str()
            pressures_path = pressures_file(BASE_DIR, date_str)
            body = read_temps_text(pressures_path)
            _client.publish(
                data_topic(CIRCUIT, "Pressure_Download"),
                f"DATE:{date_str}\n{body}",
                qos=0,
                retain=False,
            )
        elif msg.topic == data_topic(CIRCUIT, "Select_Time_Request"):
            publish_time_row(
                _client,
                BASE_DIR,
                CIRCUIT,
                SPACE_SETPOINT,
                payload,
            )
        elif msg.topic == data_topic(CIRCUIT, "Select_Range_Request"):
            publish_time_range(
                _client,
                BASE_DIR,
                CIRCUIT,
                SPACE_SETPOINT,
                payload,
            )
        elif msg.topic == data_topic(CIRCUIT, "Setpoint_Record"):
            parts = payload.split()
            if len(parts) == 2:
                _, value_raw = parts
            elif len(parts) == 1:
                value_raw = parts[0]
            else:
                return
            try:
                value = float(value_raw)
            except ValueError:
                return
            apply_setpoint(value, publish_mqtt=False, update_hmi=True)

    client.on_connect = on_connect
    client.on_message = on_message

    # Connect to MQTT with retry
    connected = False
    while not connected:
        try:
            client.connect(BROKER, PORT, keepalive=60)
            connected = True
        except socket.gaierror as exc:
            print(
                f"DNS lookup failed for MQTT broker '{BROKER}': {exc}. "
                "Retrying in 5s..."
            )
            time.sleep(5)
        except OSError as exc:
            print(
                f"MQTT connection failed to {BROKER}:{PORT}: {exc}. "
                "Retrying in 5s..."
            )
            time.sleep(5)

    client.loop_start()

    print("Starting live data collection...")
    temp_buffer: list[str] = []
    pressure_buffer: list[str] = []
    buffer_bytes = 0
    buffer_start_ts: float | None = None
    buffer_date = today_str()
    start_ts = time.time()
    try:
        while True:
            if RUN_FOR_SECONDS is not None and (time.time() - start_ts) >= RUN_FOR_SECONDS:
                print(f"Reached run limit ({RUN_FOR_SECONDS}s). Stopping data server...")
                break
            temp_line, pressure_line, pressure, line_date = read_live_line(*i2c_devices)

            if hmi:
                while True:
                    try:
                        hmi_value = hmi_setpoint_queue.get_nowait()
                    except queue.Empty:
                        break
                    apply_setpoint(hmi_value, publish_mqtt=True, update_hmi=False)

            if line_date != buffer_date and (temp_buffer or pressure_buffer):
                flush_buffers(BASE_DIR, buffer_date, temp_buffer, pressure_buffer)
                temp_buffer = []
                pressure_buffer = []
                buffer_bytes = 0
                buffer_start_ts = None
                buffer_date = line_date
            elif line_date != buffer_date:
                buffer_date = line_date

            temp_buffer.append(temp_line)
            pressure_buffer.append(pressure_line)
            buffer_bytes += len(temp_line) + len(pressure_line) + 2
            if buffer_start_ts is None:
                buffer_start_ts = time.time()

            try:
                row = parse_line(temp_line)
                publish_row(
                    client,
                    CIRCUIT,
                    row,
                    current_setpoint.get(CIRCUIT, SPACE_SETPOINT),
                    int(time.time() * 1000),
                    pressure,
                )
                if hmi:
                    hmi.set_discharge_temp(row.exv_ambient)
                if PID_ENABLED and pid is not None and row.exv_ambient == row.exv_ambient:
                    control_output = pid(row.exv_ambient)
                    apply_control_output(control_output)
            except ValueError:
                pass

            now_ts = time.time()
            if (
                buffer_bytes >= RAM_BUFFER_MAX_BYTES
                or (buffer_start_ts is not None and now_ts - buffer_start_ts >= RAM_BUFFER_MAX_SECONDS)
            ):
                flush_buffers(BASE_DIR, buffer_date, temp_buffer, pressure_buffer)
                temp_buffer = []
                pressure_buffer = []
                buffer_bytes = 0
                buffer_start_ts = None

            time.sleep(SAMPLE_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping data server...")
    finally:
        if temp_buffer or pressure_buffer:
            flush_buffers(BASE_DIR, buffer_date, temp_buffer, pressure_buffer)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
