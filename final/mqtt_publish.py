"""Live MQTT publish helpers: per-sample sensor topics, retained tables, downloads.

Replay of historical data lives in `mqtt_replay.py`.
"""

import math
from pathlib import Path

import paho.mqtt.client as mqtt

from coolprop_props import (
    COOLPROP_AVAILABLE,
    saturation_table_payload,
    state_points_payload,
)
from models import PressureRow, TempRow
from storage import (
    available_dates,
    read_temps_text,
    time_range_for_date,
)


def data_topic(circuit: str, name: str) -> str:
    """`Data/<circuit>/<name>` — used for UI requests, downloads, and replays."""
    return f"Data/{circuit}/{name}"


def publish_available_dates(client: mqtt.Client, base_dir: Path, circuit: str) -> None:
    payload = ",".join(available_dates(base_dir))
    client.publish(data_topic(circuit, "Available_Dates"), payload, qos=0, retain=True)


def publish_time_ranges(client: mqtt.Client, base_dir: Path, date_str: str, circuit: str) -> None:
    payload = time_range_for_date(base_dir, date_str)
    client.publish(data_topic(circuit, "Available_Time_Ranges"), payload, qos=0, retain=False)


def publish_saturation_table(client: mqtt.Client, circuit: str) -> None:
    """Publish the R-1234yf saturation table (retained) so late joiners get it."""
    if not COOLPROP_AVAILABLE:
        print("[CoolProp] Skipping saturation table publish — CoolProp not available.")
        return
    print("[CoolProp] Computing R-1234yf saturation table...")
    payload = saturation_table_payload(t_min_c=-40.0, t_max_c=94.0, t_step_c=2.0)
    client.publish(
        data_topic(circuit, "R1234yf_Saturation_Table"),
        payload,
        qos=0,
        retain=True,
    )
    print(f"[CoolProp] Saturation table published ({len(payload)} bytes).")


def publish_state_points(
    client: mqtt.Client,
    circuit: str,
    row: TempRow,
    pressure: PressureRow | None,
) -> None:
    """Publish live cycle state point enthalpies/entropies (per sample, not retained)."""
    if not COOLPROP_AVAILABLE or pressure is None:
        return

    def _p(pa: float) -> float:
        return pa if (pa and not math.isnan(pa)) else float("nan")

    payload = state_points_payload(
        high_T=row.high_object,        high_P=_p(pressure.high),
        low_T=row.low_object,          low_P=_p(pressure.low),
        evap_T=row.evaporator_object,  evap_P=_p(pressure.evaporator),
        exv_T=row.exv_object,          exv_P=_p(pressure.exv),
    )
    client.publish(data_topic(circuit, "R1234yf_State_Points"), payload, qos=0, retain=False)


def publish_row(
    client: mqtt.Client,
    circuit: str,
    row: TempRow,
    space_setpoint: float,
    sample_epoch_ms: int,
    pressure: PressureRow | None = None,
) -> None:
    """Publish a sample (temps, setpoint, pressures, state points)."""
    topic_values = {
        f"{circuit}/HighSide_Temperature": row.high_object,
        f"{circuit}/EXV_Temperature": row.exv_object,
        f"{circuit}/LowSide_Temperature": row.low_object,
        f"{circuit}/Evaporator_Temperature": row.evaporator_object,
        f"{circuit}/Space_Temperature": row.space_temp,
        f"{circuit}/Space_Setpoint_Temperature": space_setpoint,
        f"{circuit}/Sample_Timestamp": sample_epoch_ms,
        f"{circuit}/Discharge_Air_Temperature": row.evaporator_ambient,
    }
    for topic, value in topic_values.items():
        client.publish(topic, f"{value}", qos=0, retain=True)
    if pressure:
        client.publish(f"{circuit}/HighSide_AbsolutePressure", f"{pressure.high}", qos=0, retain=True)
        client.publish(f"{circuit}/LowSide_AbsolutePressure", f"{pressure.low}", qos=0, retain=True)
        client.publish(f"{circuit}/Evaporator_AbsolutePressure", f"{pressure.evaporator}", qos=0, retain=True)
        client.publish(f"{circuit}/EXV_AbsolutePressure", f"{pressure.exv}", qos=0, retain=True)
    publish_state_points(client, circuit, row, pressure)


def build_download_payload(path: Path, date_str: str) -> str:
    """Wrap a stored CSV file with a `DATE:` header for the website downloader."""
    body = read_temps_text(path)
    return f"DATE:{date_str}\n{body}"
