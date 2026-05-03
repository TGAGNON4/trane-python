import argparse
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

BROKER = "seniordesignmqtt.duckdns.org"
PORT = 1883
USERNAME = "dev"
PASSWORD = "trAneEseNdeS_4321"

DEFAULT_BASE_DIR = Path("/home/thomas/School/SeniorDesign/trane-scripts")
#DEFAULT_BASE_DIR = Path("/home/team6/data")
TZ = ZoneInfo("America/Chicago")

AVAILABLE_DATES_REQUEST_TOPIC = "Data/Available_Dates_Request"
AVAILABLE_DATES_TOPIC = "Data/Available_Dates"
DOWNLOAD_REQUEST_TOPIC = "Data/Download_Request"
DOWNLOAD_TOPIC = "Data/Download"
SELECT_TIME_REQUEST_TOPIC = "Data/Select_Time_Request"
SELECT_TIME_STATUS_TOPIC = "Data/Select_Time_Status"


@dataclass
class TempRow:
    timestamp: str
    high_ambient: float
    high_object: float
    low_ambient: float
    low_object: float
    evaporator_ambient: float
    evaporator_object: float
    exv_ambient: float
    exv_object: float
    space_ambient: float
    space_object: float


def parse_line(raw: str) -> TempRow:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 11:
        raise ValueError(f"expected 11 columns, got {len(parts)}")

    return TempRow(
        timestamp=parts[0],
        high_ambient=float(parts[1]),
        high_object=float(parts[2]),
        low_ambient=float(parts[3]),
        low_object=float(parts[4]),
        evaporator_ambient=float(parts[5]),
        evaporator_object=float(parts[6]),
        exv_ambient=float(parts[7]),
        exv_object=float(parts[8]),
        space_ambient=float(parts[9]),
        space_object=float(parts[10]),
    )


def current_temps_file(base_dir: Path) -> Path:
    day = datetime.now(TZ).strftime("%d-%m-%Y")
    return base_dir / day / "temps"


def dated_temps_file(base_dir: Path, date_str: str) -> Path:
    return base_dir / date_str / "temps"


def read_new_nonempty_lines(path: Path, file_pos: int) -> tuple[list[str], int]:
    if not path.exists():
        return [], file_pos

    with path.open("r", encoding="utf-8") as f:
        f.seek(0, 2)
        size = f.tell()
        if file_pos > size:
            # File was truncated/rotated.
            file_pos = 0
        f.seek(file_pos)
        lines = [line.strip() for line in f.readlines() if line.strip()]
        return lines, f.tell()


def initial_file_pos(path: Path, startup_mode: str) -> int:
    if not path.exists():
        return 0
    if startup_mode == "all":
        return 0
    return path.stat().st_size


def publish_row(
    client: mqtt.Client,
    circuit: str,
    row: TempRow,
    space_setpoint: float,
    exv_source: str,
    sample_epoch_ms: int,
) -> None:
    # Topic mapping aligns with trane-vite-app src/hooks/MQTT.ts subscriptions.
    exv_value = getattr(row, exv_source)
    topic_values = {
        f"{circuit}/HighSide_Temperature": row.high_object,
        f"{circuit}/EXV_Temperature": exv_value,
        f"{circuit}/LowSide_Temperature": row.low_object,
        f"{circuit}/Evaporator_Temperature": row.evaporator_object,
        f"{circuit}/Space_Temperature": row.space_ambient,
        f"{circuit}/Sample_Timestamp": sample_epoch_ms,
        f"{circuit}/Discharge_Air_Temperature": row.exv_object,
        f"{circuit}/Space_Setpoint_Temperature": space_setpoint,
    }

    for topic, value in topic_values.items():
        client.publish(topic, f"{value}", qos=0, retain=True)


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


def available_dates(base_dir: Path) -> list[str]:
    dates = []
    for item in sorted(base_dir.iterdir()):
        if item.is_dir() and (item / "temps").exists():
            dates.append(item.name)
    return dates


def publish_available_dates(client: mqtt.Client, base_dir: Path) -> None:
    payload = ",".join(available_dates(base_dir))
    client.publish(AVAILABLE_DATES_TOPIC, payload, qos=0, retain=True)


def read_temps_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def parse_requested_time(raw: str) -> tuple[str | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, None
    if " " in raw:
        date_str, time_str = raw.split(" ", 1)
        return date_str.strip(), time_str.strip()
    return None, raw


def publish_time_row(
    client: mqtt.Client,
    base_dir: Path,
    circuit: str,
    space_setpoint: float,
    exv_source: str,
    raw_request: str,
) -> None:
    date_str, time_str = parse_requested_time(raw_request)
    if not time_str:
        client.publish(SELECT_TIME_STATUS_TOPIC, "missing time", qos=0, retain=False)
        return
    if not date_str:
        date_str = datetime.now(TZ).strftime("%d-%m-%Y")

    temps_path = dated_temps_file(base_dir, date_str)
    if not temps_path.exists():
        client.publish(SELECT_TIME_STATUS_TOPIC, "date not found", qos=0, retain=False)
        return

    for line in temps_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = parse_line(line)
        except ValueError:
            continue
        if row.timestamp == time_str:
            publish_row(
                client,
                circuit,
                row,
                space_setpoint,
                exv_source,
                parse_sample_epoch_ms(row.timestamp, temps_path),
            )
            client.publish(SELECT_TIME_STATUS_TOPIC, "ok", qos=0, retain=False)
            return

    client.publish(SELECT_TIME_STATUS_TOPIC, "time not found", qos=0, retain=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bridge temps rows into dashboard MQTT topics."
    )
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument(
        "--date",
        help="Optional fixed date folder in dd-mm-YYYY (for replay), e.g. 05-03-2026.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Optional direct path to a temps file. Overrides --base-dir/--date.",
    )
    parser.add_argument("--circuit", default="Circuit1", help="Circuit topic prefix.")
    parser.add_argument("--broker", default=BROKER, help="MQTT broker hostname or IP.")
    parser.add_argument("--port", type=int, default=PORT, help="MQTT broker port.")
    parser.add_argument(
        "--space-setpoint",
        type=float,
        default=5.0,
        help="Published as <circuit>/Space_Setpoint_Temperature.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1.0,
        help="How often to check for a new row in temps file.",
    )
    parser.add_argument(
        "--startup-mode",
        choices=["all", "latest"],
        default="all",
        help="all=publish existing rows first, latest=only publish rows appended after startup.",
    )
    parser.add_argument(
        "--replay-delay-seconds",
        type=float,
        default=0.0,
        help="Optional delay between publishing rows during catch-up/replay.",
    )
    parser.add_argument(
        "--exv-source",
        choices=[
            "high_ambient",
            "high_object",
            "low_ambient",
            "low_object",
            "evaporator_ambient",
            "evaporator_object",
            "exv_ambient",
            "exv_object",
            "space_ambient",
            "space_object",
        ],
        default="exv_object",
        help="Which temps column to publish as EXV_Temperature.",
    )
    args = parser.parse_args()

    client = mqtt.Client(
        protocol=mqtt.MQTTv5,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(USERNAME, PASSWORD)

    def on_connect(_client, _userdata, _flags, _reason_code, _properties):
        _client.subscribe(AVAILABLE_DATES_REQUEST_TOPIC)
        _client.subscribe(DOWNLOAD_REQUEST_TOPIC)
        _client.subscribe(SELECT_TIME_REQUEST_TOPIC)

    def on_message(_client, _userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip()
        if msg.topic == AVAILABLE_DATES_REQUEST_TOPIC:
            publish_available_dates(_client, args.base_dir)
        elif msg.topic == DOWNLOAD_REQUEST_TOPIC:
            date_str = payload or datetime.now(TZ).strftime("%d-%m-%Y")
            temps_path = dated_temps_file(args.base_dir, date_str)
            _client.publish(DOWNLOAD_TOPIC, read_temps_text(temps_path), qos=0, retain=False)
        elif msg.topic == SELECT_TIME_REQUEST_TOPIC:
            publish_time_row(
                _client,
                args.base_dir,
                args.circuit,
                args.space_setpoint,
                args.exv_source,
                payload,
            )

    client.on_connect = on_connect
    client.on_message = on_message
    connected = False
    while not connected:
        try:
            client.connect(args.broker, args.port, keepalive=60)
            connected = True
        except socket.gaierror as exc:
            print(
                f"DNS lookup failed for MQTT broker '{args.broker}': {exc}. "
                "Check network/DNS or pass an IP with --broker. Retrying in 5s..."
            )
            time.sleep(5)
        except OSError as exc:
            print(
                f"MQTT connection failed to {args.broker}:{args.port}: {exc}. "
                "Retrying in 5s..."
            )
            time.sleep(5)
    client.loop_start()

    '''print(
        f"Publishing {args.circuit} from {args.base_dir} every {args.poll_seconds}s "
        f"to {args.broker}:{args.port}"
    )'''

    if args.file:
        active_path = args.file
        follow_day_rollover = False
    elif args.date:
        active_path = dated_temps_file(args.base_dir, args.date)
        follow_day_rollover = False
    else:
        active_path = current_temps_file(args.base_dir)
        follow_day_rollover = True

    file_pos = initial_file_pos(active_path, args.startup_mode)

    try:
        while True:
            temps_path = current_temps_file(args.base_dir)
            if follow_day_rollover and temps_path != active_path:
                active_path = temps_path
                file_pos = initial_file_pos(active_path, args.startup_mode)
                print(f"Switched to new daily file: {active_path}")

            lines, file_pos = read_new_nonempty_lines(active_path, file_pos)
            for line in lines:
                try:
                    row = parse_line(line)
                    publish_row(
                        client,
                        args.circuit,
                        row,
                        args.space_setpoint,
                        args.exv_source,
                        parse_sample_epoch_ms(row.timestamp, active_path),
                    )
                    print(f"Published {args.circuit} row at {row.timestamp}")
                except ValueError as exc:
                    print(f"Skipping malformed row from {active_path}: {exc}")
                if args.replay_delay_seconds > 0:
                    time.sleep(args.replay_delay_seconds)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("Stopping bridge...")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
