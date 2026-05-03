import json
import time

import paho.mqtt.client as mqtt

BROKER = "seniordesignmqtt.duckdns.org"
PORT = 1883
USERNAME = "dev"
PASSWORD = "trAneEseNdeS_4321"

PROBE_TOPIC = "latency/probe"
ACK_TOPIC = "latency/ack"
ACK_SUBSCRIBE = "latency/ack/#"

CIRCUITS = ["Circuit1", "Circuit2"]
TARGET_ACKS = 10
THRESHOLD_MS = 1000.0
TIMEOUT_SECONDS = 60

pending = {}
results = {circuit: [] for circuit in CIRCUITS}
probe_seen = 0
ack_seen = 0


def parse_payload(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"Connect failed: {reason_code}")
        return
    client.subscribe(PROBE_TOPIC)
    client.subscribe(ACK_SUBSCRIBE)


def on_message(client, userdata, msg):
    global probe_seen, ack_seen

    payload = parse_payload(msg.payload)
    if not payload:
        return

    msg_id = payload.get("id")
    circuit = payload.get("circuit")
    if not msg_id or circuit not in results:
        return

    if msg.topic == PROBE_TOPIC:
        probe_seen += 1
        sent_ms = payload.get("sent_ms")
        if isinstance(sent_ms, (int, float)):
            pending[msg_id] = {"circuit": circuit, "sent_ms": float(sent_ms)}
        return

    if msg.topic.startswith(ACK_TOPIC):
        ack_seen += 1
        item = pending.pop(msg_id, None)
        if not item:
            return

        latency_ms = time.time() * 1000.0 - item["sent_ms"]
        results[item["circuit"]].append(latency_ms)


client = mqtt.Client(protocol=mqtt.MQTTv5, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(USERNAME, PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, PORT, keepalive=60)
client.loop_start()

start = time.time()
while time.time() - start < TIMEOUT_SECONDS:
    if all(len(results[c]) >= TARGET_ACKS for c in CIRCUITS):
        break
    time.sleep(0.1)

client.loop_stop()
client.disconnect()

for circuit in CIRCUITS:
    values = results[circuit]
    over = sum(1 for v in values if v >= THRESHOLD_MS)
    ok = len(values) >= TARGET_ACKS and over == 0

    if values:
        avg = sum(values) / len(values)
        print(
            f"{circuit}: {'PASS' if ok else 'FAIL'} | "
            f"acks {len(values)}/{TARGET_ACKS} | "
            f"avg {avg:.2f} ms | >={THRESHOLD_MS:.0f} ms: {over}"
        )
    else:
        print(f"{circuit}: FAIL | acks 0/{TARGET_ACKS}")

if ack_seen == 0:
    print("\nNo ack messages were received.")
    print(f"Expected website ack topic: {ACK_TOPIC} (or subtopic under {ACK_SUBSCRIBE})")
    print('Expected ack payload: {"id":"<probe id>","circuit":"Circuit1|Circuit2"}')
    print(f"Probes seen from simulator: {probe_seen}")
    if pending:
        sample = next(iter(pending.keys()))
        print(f"Example pending probe id: {sample}")
