"""I2C sensor setup and NaN-safe read helpers.

Hardware:
    - 4× MLX90614 IR thermometers (HighSide / LowSide / Evaporator / EXV).
      Evaporator ambient = discharge air temperature (PID input).
    - 1× ADS1115 4-channel ADC reading ratiometric 0.5–4.5 V pressure sensors
      (0–300 PSI); channels map to high/EXV/low/evaporator (Pa).

A bad sensor or I2C glitch returns NaN instead of raising.
"""

from datetime import datetime

from config import (
    ADS_ADDR,
    DISCONNECT_VOLTAGE,
    P_MAX_PSI,
    P_V_MAX,
    P_V_MIN,
    SENSORS,
    TZ,
)
from models import PressureRow

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


def init_i2c_devices():
    """Initialize I2C bus, ADS1115, and MLX sensors.

    Returns None if libraries aren't installed; returns placeholder None devices
    when hardware is missing so that reads simply yield NaN.
    """
    if board is None or busio is None or ADS is None or AnalogIn is None or adafruit_mlx90614 is None:
        return None
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
    except Exception as exc:
        print(f"I2C bus init failed ({exc}); running with no sensors (all NaN)")
        return [None, None, None, None], None, None, None, None
    try:
        ads = ADS.ADS1115(i2c, address=ADS_ADDR)
        ads.gain = 2 / 3  # ±6.144 V range — required to read 4.5 V full-scale output
        a0 = AnalogIn(ads, 0)
        a1 = AnalogIn(ads, 1)
        a2 = AnalogIn(ads, 2)
        a3 = AnalogIn(ads, 3)
    except Exception as exc:
        print(f"ADS1115 init failed ({exc}); pressure channels will read NaN")
        a0 = a1 = a2 = a3 = None
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
    if volts != volts:
        return float("nan")
    psi = (volts - P_V_MIN) / (P_V_MAX - P_V_MIN) * P_MAX_PSI
    psi = max(0.0, min(P_MAX_PSI, psi))
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


def read_live_line(mlxs, a0, a1, a2, a3) -> tuple[str, str, PressureRow, str]:
    """Read all sensors; return (temp_csv, pressure_csv, PressureRow, date_str)."""
    now = datetime.now(TZ)
    current_time = now.strftime("%H:%M:%S")
    date_str = now.strftime("%d-%m-%Y")
    high_amb, high_obj = safe_read_temp_c(mlxs[0])
    low_amb, low_obj = safe_read_temp_c(mlxs[1])
    evap_amb, evap_obj = safe_read_temp_c(mlxs[2])  # evap_amb is discharge air (PID input)
    exv_amb, exv_obj = safe_read_temp_c(mlxs[3])

    high_p = safe_read_pressure_pa(a0)
    low_p = safe_read_pressure_pa(a2)
    evap_p = safe_read_pressure_pa(a3)
    exv_p = safe_read_pressure_pa(a1)

    space_temp = (high_amb + low_amb + evap_amb + exv_amb) / 4

    temp_readings = [
        current_time,
        high_amb, high_obj,
        low_amb, low_obj,
        evap_amb, evap_obj,
        exv_amb, exv_obj,
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
