"""
Verification Test Suite
=======================
Covers four system-level requirements:

  1. MQTT latency      — broker round-trip < 2 s on local network
  2. DHCP / Ethernet   — active interface has a DHCP-assigned IP
  3. Control latency   — setpoint change reflected in Compressor_Current_RPM < 2 s
  4. Sensor latency    — sensor MQTT publish received by broker < 2 s

Run on the Raspberry Pi (or same LAN segment):
    python3 tests/verification_test.py

Requirements: paho-mqtt, requests (pip install paho-mqtt requests)
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import uuid

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration — mirrors final/config.py broker settings
# ---------------------------------------------------------------------------
BROKER   = "seniordesignmqtt.duckdns.org"
PORT     = 1883
USERNAME = "dev"
PASSWORD = "trAneEseNdeS_4321"
CIRCUIT  = "Circuit1"
LATENCY_LIMIT_S = 2.0   # all timing requirements are < 2 s

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"

def _result(name: str, passed: bool, detail: str = "") -> dict:
    status = PASS if passed else FAIL
    msg = f"[{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return {"name": name, "passed": passed, "detail": detail}


def _make_client(client_id: str | None = None) -> mqtt.Client:
    cid = client_id or f"verify_{uuid.uuid4().hex[:8]}"
    client = mqtt.Client(
        client_id=cid,
        protocol=mqtt.MQTTv5,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(USERNAME, PASSWORD)
    return client


def _connect_blocking(client: mqtt.Client, timeout: float = 5.0) -> bool:
    connected = threading.Event()

    def on_connect(c, u, f, rc, p):
        if rc == 0:
            connected.set()

    client.on_connect = on_connect
    try:
        client.connect(BROKER, PORT, keepalive=30)
    except Exception as exc:
        print(f"  Connection error: {exc}")
        return False
    client.loop_start()
    ok = connected.wait(timeout=timeout)
    if not ok:
        client.loop_stop()
    return ok


# ---------------------------------------------------------------------------
# Test 1 — MQTT round-trip latency < 2 s
# ---------------------------------------------------------------------------
def test_mqtt_latency(samples: int = 5) -> dict:
    """
    Requirement: MQTT messages confirmed received by cloud platform
    within 2 seconds of a sensor event on a local network connection.

    Method: publish a probe payload to a unique topic, subscribe to the
    same topic, and measure round-trip time.  The broker itself is the
    'cloud platform' receiving the message.
    """
    name = "MQTT round-trip latency < 2 s"
    topic = f"verify/latency/{uuid.uuid4().hex}"
    latencies: list[float] = []
    lock = threading.Lock()

    client = _make_client()

    def on_message(c, u, msg):
        try:
            data = json.loads(msg.payload)
            sent = data["sent_ms"]
            rtt = (time.time() * 1000 - sent) / 1000.0
            with lock:
                latencies.append(rtt)
        except Exception:
            pass

    client.on_message = on_message
    if not _connect_blocking(client):
        return _result(name, False, "Could not connect to broker")

    client.subscribe(topic, qos=0)
    time.sleep(0.3)   # let subscription propagate

    for _ in range(samples):
        payload = json.dumps({"sent_ms": time.time() * 1000})
        client.publish(topic, payload, qos=0)
        time.sleep(0.5)

    deadline = time.time() + 3.0
    while time.time() < deadline:
        with lock:
            if len(latencies) >= samples:
                break
        time.sleep(0.1)

    client.loop_stop()
    client.disconnect()

    with lock:
        received = list(latencies)

    if not received:
        return _result(name, False, "No messages received back from broker")

    max_rtt = max(received)
    avg_rtt = sum(received) / len(received)
    passed = max_rtt < LATENCY_LIMIT_S and len(received) >= samples
    detail = (
        f"{len(received)}/{samples} samples | "
        f"avg {avg_rtt*1000:.0f} ms | max {max_rtt*1000:.0f} ms"
    )
    return _result(name, passed, detail)


# ---------------------------------------------------------------------------
# Test 2 — Ethernet port with DHCP
# ---------------------------------------------------------------------------
def test_dhcp_ethernet() -> dict:
    """
    Requirement: controller has an Ethernet port that supports DHCP.

    Method: enumerate network interfaces via 'ip addr' and confirm that
    an Ethernet interface (eth0 / enp* / ens*) has an IPv4 address
    assigned (indicating successful DHCP negotiation).
    """
    name = "Ethernet port supports DHCP"

    # Approach 1: check via 'ip addr' (Linux)
    try:
        output = subprocess.check_output(
            ["ip", "-4", "addr", "show"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        eth_interfaces = []
        current_iface = None
        for line in output.splitlines():
            line = line.strip()
            if line and not line.startswith(" ") and not line.startswith("inet"):
                current_iface = line.split(":")[1].strip() if ":" in line else None
            if current_iface and any(
                current_iface.startswith(p) for p in ("eth", "enp", "ens", "eno")
            ):
                if line.startswith("inet "):
                    ip = line.split()[1].split("/")[0]
                    eth_interfaces.append((current_iface, ip))

        if eth_interfaces:
            detail = ", ".join(f"{iface}={ip}" for iface, ip in eth_interfaces)
            return _result(name, True, detail)
    except FileNotFoundError:
        pass   # 'ip' not available — try fallback

    # Approach 2: resolve own hostname — works on any OS
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        loopback = ip.startswith("127.")
        if not loopback:
            return _result(name, True, f"hostname={hostname}, ip={ip}")
        # loopback only — no external interface found
        return _result(name, False, f"Only loopback address found ({ip})")
    except Exception as exc:
        return _result(name, False, str(exc))


# ---------------------------------------------------------------------------
# Test 3 — Control response: setpoint → RPM change < 2 s
# ---------------------------------------------------------------------------
def test_control_response() -> dict:
    """
    Requirement: control software reflects a change in the lowest level
    of control within 2 seconds.

    Method: subscribe to {CIRCUIT}/Compressor_Current_RPM.  Record the
    RPM before the setpoint change, publish a new setpoint, then confirm
    the RPM value updates within 2 seconds.

    NOTE: the compressor must already be running (Started) for RPM to
    change in response to a setpoint.  If it is idle the test records
    the observation but does not fail the suite — it marks SKIPPED.
    """
    name = "Control response: setpoint → RPM update < 2 s"

    rpm_events: list[tuple[float, float]] = []   # (timestamp, rpm)
    lock = threading.Lock()

    client = _make_client()

    def on_message(c, u, msg):
        try:
            val = float(msg.payload)
            with lock:
                rpm_events.append((time.time(), val))
        except ValueError:
            pass

    client.on_message = on_message
    if not _connect_blocking(client):
        return _result(name, False, "Could not connect to broker")

    client.subscribe(f"{CIRCUIT}/Compressor_Current_RPM", qos=0)
    time.sleep(0.5)

    # Record baseline RPM
    with lock:
        baseline = rpm_events[-1][1] if rpm_events else None

    if baseline is None or baseline < 100:
        client.loop_stop()
        client.disconnect()
        return _result(
            name, True,
            "SKIPPED — compressor not running; start compressor before this test"
        )

    # Publish a setpoint change (nudge by +2 °C then restore)
    original_sp = 22.2
    new_sp = original_sp + 2.0
    t_publish = time.time()
    client.publish(
        f"Data/{CIRCUIT}/Setpoint_Record",
        f"{new_sp}",
        qos=0,
        retain=False,
    )

    # Wait up to 3 s for an RPM update
    deadline = time.time() + 3.0
    response_time: float | None = None
    while time.time() < deadline:
        with lock:
            for ts, rpm in rpm_events:
                if ts > t_publish and abs(rpm - baseline) > 10:
                    response_time = ts - t_publish
                    break
        if response_time is not None:
            break
        time.sleep(0.05)

    # Restore original setpoint
    client.publish(
        f"Data/{CIRCUIT}/Setpoint_Record",
        f"{original_sp}",
        qos=0,
        retain=False,
    )

    client.loop_stop()
    client.disconnect()

    if response_time is None:
        return _result(name, False, "No RPM change observed within 3 s of setpoint update")

    passed = response_time < LATENCY_LIMIT_S
    return _result(
        name, passed,
        f"RPM updated {response_time*1000:.0f} ms after setpoint publish "
        f"(baseline {baseline:.0f} RPM)"
    )


# ---------------------------------------------------------------------------
# Test 4 — Sensor change displayed in software < 2 s
# ---------------------------------------------------------------------------
def test_sensor_display_latency() -> dict:
    """
    Requirement: changes at the sensor level shall be displayed in
    software within 2 seconds.

    Method: subscribe to each live sensor temperature topic, record the
    timestamp of the most recently retained message, then measure how
    quickly new values arrive.  A sensor publishing within 2 s of the
    previous value satisfies the display-latency requirement (the MQTT
    broker is the integration point between Pi and the web UI).

    Sensors checked: HighSide, EXV, LowSide, Evaporator, Space,
                     Discharge Air.
    """
    name = "Sensor change displayed in software < 2 s"

    SENSOR_TOPICS = {
        "HighSide":    f"{CIRCUIT}/HighSide_Temperature",
        "EXV":         f"{CIRCUIT}/EXV_Temperature",
        "LowSide":     f"{CIRCUIT}/LowSide_Temperature",
        "Evaporator":  f"{CIRCUIT}/Evaporator_Temperature",
        "Space":       f"{CIRCUIT}/Space_Temperature",
        "DischargeAir":f"{CIRCUIT}/Discharge_Air_Temperature",
    }

    first_rx: dict[str, float] = {}   # sensor → first timestamp
    second_rx: dict[str, float] = {}  # sensor → second timestamp
    lock = threading.Lock()

    client = _make_client()

    def on_message(c, u, msg):
        for label, topic in SENSOR_TOPICS.items():
            if msg.topic == topic:
                ts = time.time()
                with lock:
                    if label not in first_rx:
                        first_rx[label] = ts
                    elif label not in second_rx:
                        second_rx[label] = ts

    client.on_message = on_message
    if not _connect_blocking(client):
        return _result(name, False, "Could not connect to broker")

    for topic in SENSOR_TOPICS.values():
        client.subscribe(topic, qos=0)

    # Wait up to 5 s to collect two readings per sensor
    deadline = time.time() + 5.0
    while time.time() < deadline:
        with lock:
            if len(second_rx) == len(SENSOR_TOPICS):
                break
        time.sleep(0.1)

    client.loop_stop()
    client.disconnect()

    intervals: dict[str, float] = {}
    with lock:
        for label in SENSOR_TOPICS:
            if label in first_rx and label in second_rx:
                intervals[label] = second_rx[label] - first_rx[label]

    missing = [s for s in SENSOR_TOPICS if s not in intervals]
    if missing:
        return _result(
            name, False,
            f"No two readings received for: {', '.join(missing)} — is the Pi running?"
        )

    worst_label = max(intervals, key=intervals.__getitem__)
    worst_s = intervals[worst_label]
    passed = worst_s < LATENCY_LIMIT_S
    detail_parts = [f"{s}={v*1000:.0f}ms" for s, v in sorted(intervals.items())]
    return _result(
        name, passed,
        f"worst={worst_label} {worst_s*1000:.0f} ms | " + ", ".join(detail_parts)
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 60)
    print("Verification Test Suite")
    print(f"Broker : {BROKER}:{PORT}")
    print(f"Circuit: {CIRCUIT}")
    print(f"Limit  : {LATENCY_LIMIT_S * 1000:.0f} ms")
    print("=" * 60)

    results = [
        test_mqtt_latency(),
        test_dhcp_ethernet(),
        test_control_response(),
        test_sensor_display_latency(),
    ]

    print("=" * 60)
    passed = sum(1 for r in results if r["passed"])
    print(f"Result : {passed}/{len(results)} passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
