"""MQTT `on_connect` and `on_message` handlers, bound to an `Application`.

Kept in a dedicated module so `app.py` stays focused on lifecycle and the
sample loop. `attach(app)` wires both callbacks onto `app.client`.
"""

from __future__ import annotations

from config import (
    BASE_DIR,
    CIRCUIT,
    HMI_ENABLED,
    SPACE_SETPOINT,
    VFD_MAX_RPM,
    VFD_MIN_RPM,
    VFD_RPM_AT_MIN_SPEED,
)
from mqtt_publish import (
    build_download_payload,
    data_topic,
    publish_available_dates,
    publish_saturation_table,
    publish_time_ranges,
)
from mqtt_replay import publish_time_range, publish_time_row
from storage import pressures_file, setpoints_file, today_str


def attach(app) -> None:
    app.client.on_connect = lambda c, u, f, rc, p: _on_connect(app, c)
    app.client.on_message = lambda c, u, msg: _on_message(app, c, msg)


def _on_connect(app, client) -> None:
    print("Connected to MQTT broker")
    for name in (
        "Available_Dates_Request",
        "Available_Time_Ranges_Request",
        "Temperature_Download_Request",
        "Pressure_Download_Request",
        "Setpoint_Download_Request",
        "Select_Time_Request",
        "Select_Range_Request",
        "Setpoint_Record",
        "Compressor_RPM",
        "Compressor_Shutdown",
        "Compressor_Start",
        "Unit_Change",
    ):
        client.subscribe(data_topic(CIRCUIT, name))
    client.subscribe(f"{CIRCUIT}/Compressor_RPM")

    client.publish(f"{CIRCUIT}/Session_Lock", "", qos=0, retain=True)
    client.publish(f"{CIRCUIT}/VFD_Min_RPM", str(VFD_MIN_RPM), qos=0, retain=True)
    client.publish(f"{CIRCUIT}/VFD_Max_RPM", str(VFD_MAX_RPM), qos=0, retain=True)

    unit = app.hmi.get_unit() if app.hmi is not None else app.display_unit
    client.publish(f"{CIRCUIT}/Unit", unit, qos=0, retain=True)
    publish_available_dates(client, BASE_DIR, CIRCUIT)
    publish_time_ranges(client, BASE_DIR, today_str(), CIRCUIT)
    publish_saturation_table(client, CIRCUIT)
    if HMI_ENABLED:
        client.publish(f"{CIRCUIT}/HMI_Status", "1" if app.hmi is not None else "0", qos=0, retain=True)


def _on_message(app, client, msg) -> None:
    payload = msg.payload.decode(errors="ignore").strip()
    topic = msg.topic

    if topic == data_topic(CIRCUIT, "Available_Dates_Request"):
        publish_available_dates(client, BASE_DIR, CIRCUIT)
    elif topic == data_topic(CIRCUIT, "Available_Time_Ranges_Request"):
        publish_time_ranges(client, BASE_DIR, payload or today_str(), CIRCUIT)
    elif topic == data_topic(CIRCUIT, "Temperature_Download_Request"):
        date_str = payload or today_str()
        client.publish(
            data_topic(CIRCUIT, "Temperature_Download"),
            build_download_payload(BASE_DIR / date_str / "temps", date_str),
            qos=0, retain=False,
        )
    elif topic == data_topic(CIRCUIT, "Pressure_Download_Request"):
        date_str = payload or today_str()
        client.publish(
            data_topic(CIRCUIT, "Pressure_Download"),
            build_download_payload(pressures_file(BASE_DIR, date_str), date_str),
            qos=0, retain=False,
        )
    elif topic == data_topic(CIRCUIT, "Setpoint_Download_Request"):
        date_str = payload or today_str()
        client.publish(
            data_topic(CIRCUIT, "Setpoint_Download"),
            build_download_payload(setpoints_file(BASE_DIR, date_str), date_str),
            qos=0, retain=False,
        )
    elif topic == data_topic(CIRCUIT, "Select_Time_Request"):
        publish_time_row(client, BASE_DIR, CIRCUIT, SPACE_SETPOINT, payload)
    elif topic == data_topic(CIRCUIT, "Select_Range_Request"):
        publish_time_range(client, BASE_DIR, CIRCUIT, SPACE_SETPOINT, payload)
    elif topic == data_topic(CIRCUIT, "Setpoint_Record"):
        _handle_setpoint_record(app, payload)
    elif topic == data_topic(CIRCUIT, "Compressor_Shutdown"):
        app.request_shutdown("MQTT")
    elif topic == data_topic(CIRCUIT, "Compressor_Start"):
        app.request_start("MQTT")
    elif topic == data_topic(CIRCUIT, "Unit_Change"):
        unit = payload.upper()
        if unit in ("C", "F") and unit != app.display_unit:
            app.set_unit(unit, push_to_hmi=True)
    elif topic == f"{CIRCUIT}/Compressor_RPM" or topic == data_topic(CIRCUIT, "Compressor_RPM"):
        _handle_compressor_rpm(app, payload)


def _handle_setpoint_record(app, payload: str) -> None:
    parts = payload.split()
    if len(parts) == 2 and parts[0] == CIRCUIT:
        value_raw, update_hmi = parts[1], False
    elif len(parts) == 1:
        value_raw, update_hmi = parts[0], True
    else:
        return
    try:
        value = float(value_raw)
    except ValueError:
        return
    app.apply_setpoint(value, publish_mqtt=False, update_hmi=update_hmi)


def _handle_compressor_rpm(app, payload: str) -> None:
    raw = payload.lower()
    if raw in ("", "none", "null"):
        app.compressor.set_override_rpm(None)
        print("Cleared RPM override via MQTT")
        return
    try:
        rpm_val = float(payload)
    except ValueError:
        return
    rpm_val = max(VFD_MIN_RPM, min(VFD_MAX_RPM, rpm_val))
    app.compressor.set_override_rpm(rpm_val)
    print(f"Set RPM override via MQTT: {rpm_val}")
