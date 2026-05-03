import paho.mqtt.client as mqtt
import json
import random
import threading
import time
import uuid

# -------------------------------
# Default State per Circuit
# -------------------------------
def make_sensor_state():
    return {
        "HighSide_Temperature": 35.0,
        "HighSide_AbsolutePressure": 1500000.0,
        "EXV_Temperature": 30.0,
        "EXV_AbsolutePressure": 1400000.0,
        "LowSide_Temperature": 10.0,
        "LowSide_AbsolutePressure": 500000.0,
        "Evaporator_Temperature": 15.0,
        "Evaporator_AbsolutePressure": 550000.0,
        "Space_Temperature": 25.0,
        "Discharge_Air_Temperature": 5.0,
        "Space_Setpoint_Temperature": 5.0,
    }

# Initialize 2 Circuits
sensor_data = {
    "Circuit1": make_sensor_state(),
    "Circuit2": make_sensor_state(),
}

# -------------------------------
# MQTT SETTINGS
# -------------------------------
MQTT_BROKER = "seniordesignmqtt.duckdns.org"
MQTT_PORT = 1883
MQTT_USER = "dev"
MQTT_PASS = "trAneEseNdeS_4321"
USE_TLS = False
USE_WEBSOCKETS = False
WEBSOCKETS_PATH = None


SENSOR_TOPICS = [
    "HighSide_Temperature",
    "HighSide_AbsolutePressure",
    "EXV_Temperature",
    "EXV_AbsolutePressure",
    "LowSide_Temperature",
    "LowSide_AbsolutePressure",
    "Evaporator_Temperature",
    "Evaporator_AbsolutePressure",
    "Space_Temperature",
    "Discharge_Air_Temperature",
]

LATENCY_PROBE_TOPIC = "latency/probe"

# When connected to website
def on_connect(client, userdata, flags, reason_code, properties):
    print("MQTT connected:", reason_code)
    for circuit in sensor_data.keys():
        topic = f"{circuit}/Space_Setpoint_Temperature"
        client.subscribe(topic)
        print(f"Subscribed to: {topic}")


def on_message(client, userdata, msg):
    for circuit in sensor_data.keys():
        sp_topic = f"{circuit}/Space_Setpoint_Temperature"
        if msg.topic == sp_topic:
            try:
                new_sp = float(msg.payload.decode())
                sensor_data[circuit]["Space_Setpoint_Temperature"] = new_sp
            except ValueError:
                print("Invalid setpoint received:", msg.payload)


mqtt_receiver = mqtt.Client(
    protocol=mqtt.MQTTv5,
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    transport="websockets" if USE_WEBSOCKETS else "tcp",
)
mqtt_receiver.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_receiver.on_connect = on_connect
mqtt_receiver.on_message = on_message
if USE_TLS:
    mqtt_receiver.tls_set()
if USE_WEBSOCKETS and WEBSOCKETS_PATH:
    mqtt_receiver.ws_set_options(path=WEBSOCKETS_PATH)
mqtt_receiver.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
threading.Thread(target=mqtt_receiver.loop_forever, daemon=True).start()


def jitter(value, spread):
    return value + random.uniform(-spread, spread)


def mqtt_publish_loop():
    client = mqtt.Client(
        protocol=mqtt.MQTTv5,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        transport="websockets" if USE_WEBSOCKETS else "tcp",
    )
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    if USE_TLS:
        client.tls_set()
    if USE_WEBSOCKETS and WEBSOCKETS_PATH:
        client.ws_set_options(path=WEBSOCKETS_PATH)
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    while True:
        for circuit, values in sensor_data.items():
            values["HighSide_Temperature"] = jitter(values["HighSide_Temperature"], 0.5)
            values["HighSide_AbsolutePressure"] = jitter(values["HighSide_AbsolutePressure"], 5000.0)

            values["EXV_Temperature"] = jitter(values["EXV_Temperature"], 0.5)
            values["EXV_AbsolutePressure"] = jitter(values["EXV_AbsolutePressure"], 5000.0)

            values["LowSide_Temperature"] = jitter(values["LowSide_Temperature"], 0.5)
            values["LowSide_AbsolutePressure"] = jitter(values["LowSide_AbsolutePressure"], 5000.0)

            values["Evaporator_Temperature"] = jitter(values["Evaporator_Temperature"], 0.5)
            values["Evaporator_AbsolutePressure"] = jitter(values["Evaporator_AbsolutePressure"], 5000.0)

            values["Space_Temperature"] = jitter(values["Space_Temperature"], 0.2)
            values["Discharge_Air_Temperature"] = jitter(values["Discharge_Air_Temperature"], 0.3)

            for topic in SENSOR_TOPICS:
                client.publish(f"{circuit}/{topic}", values[topic])

            # Also publish setpoint so graph line is visible on first load
            client.publish(f"{circuit}/Space_Setpoint_Temperature", values["Space_Setpoint_Temperature"], retain=True)

            # Probe used for end-to-end website latency tests.
            probe = {
                "id": str(uuid.uuid4()),
                "circuit": circuit,
                "sent_ms": int(time.time() * 1000),
            }
            client.publish(LATENCY_PROBE_TOPIC, json.dumps(probe))

        time.sleep(1)


threading.Thread(target=mqtt_publish_loop, daemon=True).start()


try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Exiting simulator...")
