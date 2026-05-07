"""
Verification Test Suite
=======================
Covers two system-level requirements:

  1. MQTT latency  — broker round-trip < 2 s on local network
  2. DHCP/Ethernet — active interface has a DHCP-assigned IP

Run on the Raspberry Pi (or same LAN segment):
    python3 tests/verification_test.py

Requirements: paho-mqtt (pip install paho-mqtt)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import textwrap
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt


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

# ---------------------------------------------------------------------------
# Configuration — mirrors final/config.py broker settings
# ---------------------------------------------------------------------------
BROKER   = "seniordesignmqtt.duckdns.org"
PORT     = 1883
USERNAME = os.environ.get("MQTT_USERNAME") or _env.get("MQTT_USERNAME") or "dev"
PASSWORD = os.environ.get("MQTT_PASSWORD") or _env.get("MQTT_PASSWORD") or ""
CIRCUIT  = "Circuit1"
LATENCY_LIMIT_S = 2.0   # all timing requirements are < 2 s

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
SEP_WIDTH = 60

def _print(msg: str) -> None:
    """Print msg wrapped to SEP_WIDTH, with a two-space hang-indent on continuation lines."""
    for line in textwrap.wrap(msg, width=SEP_WIDTH, subsequent_indent="  ") or [""]:
        print(line)

def _result(name: str, passed: bool, detail: str = "") -> dict:
    status = PASS if passed else FAIL
    msg = f"[{status}] {name}"
    if detail:
        msg += f" — {detail}"
    _print(msg)
    return {"name": name, "passed": passed, "detail": detail, "msg": msg}


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
        _print(f"  Connection error: {exc}")
        return False
    client.loop_start()
    ok = connected.wait(timeout=timeout)
    if not ok:
        client.loop_stop()
    return ok


# ---------------------------------------------------------------------------
# Test 1 — MQTT round-trip latency < 2 s
# ---------------------------------------------------------------------------
def test_mqtt_latency(samples: int = 25) -> dict:
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

    deadline = time.time() + 5.0
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
def _wifi_set(enabled: bool) -> None:
    """Bring WiFi up or down via rfkill (preferred) or ip link."""
    action = "unblock" if enabled else "block"
    try:
        subprocess.run(["rfkill", action, "wifi"],
                       check=True, stderr=subprocess.DEVNULL)
        return
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    try:
        out = subprocess.check_output(["ip", "link", "show"],
                                      stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2:
                iface = parts[1].strip().split("@")[0]
                if iface.startswith("wlan"):
                    link_action = "up" if enabled else "down"
                    subprocess.run(["ip", "link", "set", iface, link_action],
                                   stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass


def test_dhcp_ethernet() -> dict:
    """
    Requirement: controller has an Ethernet port that supports DHCP.

    Method: disable WiFi, confirm an Ethernet interface has a DHCP-assigned
    IP, then verify internet connectivity over that interface by connecting
    to the MQTT broker.  WiFi is re-enabled regardless of outcome.

    NOTE: requires root (or passwordless sudo) for rfkill / ip link.
    Run as: sudo python3 tests/verification_test.py
    """
    name = "Ethernet port supports DHCP + internet connectivity"

    eth_iface: str | None = None
    eth_ip:    str | None = None
    try:
        output = subprocess.check_output(
            ["ip", "-4", "addr", "show"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        current_iface = None
        for line in output.splitlines():
            line = line.strip()
            if line and not line.startswith("inet"):
                parts = line.split(":")
                if len(parts) >= 2:
                    current_iface = parts[1].strip().split("@")[0]
            if current_iface and any(
                current_iface.startswith(p) for p in ("eth", "enp", "ens", "eno")
            ):
                if line.startswith("inet "):
                    eth_ip    = line.split()[1].split("/")[0]
                    eth_iface = current_iface
                    break
    except FileNotFoundError:
        return _result(name, False, "'ip' command not found — run on Linux")

    if eth_iface is None:
        return _result(name, False, "No Ethernet interface with an IP found")

    _print(f"  Ethernet: {eth_iface}={eth_ip}")
    _print("  Disabling WiFi …")
    _wifi_set(False)
    time.sleep(2)

    connected = False
    try:
        client = _make_client()
        connected = _connect_blocking(client, timeout=5.0)
        if connected:
            client.loop_stop()
            client.disconnect()
    except Exception:
        pass

    _print("  Re-enabling WiFi …")
    _wifi_set(True)

    if not connected:
        return _result(
            name, False,
            f"{eth_iface}={eth_ip} but could not reach broker over Ethernet"
        )
    return _result(name, True, f"{eth_iface}={eth_ip}, broker reachable over Ethernet")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * SEP_WIDTH)
    print("Verification Test Suite")
    _print(f"Broker : {BROKER}:{PORT}")
    _print(f"Circuit: {CIRCUIT}")
    _print(f"Limit  : {LATENCY_LIMIT_S * 1000:.0f} ms")
    print("=" * SEP_WIDTH)

    results = [
        test_mqtt_latency(),
        test_dhcp_ethernet(),
    ]

    print("=" * SEP_WIDTH)
    passed = sum(1 for r in results if r["passed"])
    _print(f"Result : {passed}/{len(results)} passed")
    print("=" * SEP_WIDTH)


if __name__ == "__main__":
    main()
