"""Replay of stored history onto the live MQTT topics.

Lets the website scrub through past samples by re-publishing them as if they
arrived in real time.
"""

from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

from config import TZ
from mqtt_publish import data_topic, publish_row
from storage import (
    load_pressures,
    load_setpoints,
    parse_line,
    parse_requested_range,
    parse_requested_time,
    today_str,
)


def parse_sample_epoch_ms(row_timestamp: str, active_path: Path) -> int:
    """Combine the sample's HH:MM:SS with the folder's date into epoch ms."""
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


def publish_time_row(
    client: mqtt.Client,
    base_dir: Path,
    circuit: str,
    space_setpoint: float,
    raw_request: str,
) -> None:
    """Replay a single historical row at the requested timestamp."""
    circuit_name, date_str, time_str = parse_requested_time(raw_request)
    if not time_str:
        client.publish(data_topic(circuit, "Select_Time_Status"), "missing time", qos=0, retain=False)
        return
    if not date_str:
        date_str = today_str()
    if not circuit_name:
        circuit_name = circuit

    temps_path = base_dir / date_str / "temps"
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
    """Replay all historical rows in the requested time range."""
    circuit_name, date_str, start_time, end_time = parse_requested_range(raw_request)
    if not start_time or not end_time:
        client.publish(data_topic(circuit, "Select_Range_Status"), "missing range", qos=0, retain=False)
        return
    if not date_str:
        date_str = today_str()
    if not circuit_name:
        circuit_name = circuit

    temps_path = base_dir / date_str / "temps"
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
