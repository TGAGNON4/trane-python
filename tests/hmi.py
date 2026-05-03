import serial
import threading
import time

# --- Config ---
SERIAL_PORT = "/dev/ttyS0"
BAUD_RATE = 9600

# --- State (all °C) ---
state = {
    "discharge_temp": 20.0,
    "setpoint_temp": 5.0,
    "unit": "C",
    "synced": False,
}
state_lock = threading.Lock()
ser = None

# --- Unit conversion ---
def to_display(celsius, unit):
    if unit == "F":
        return round(celsius * 9 / 5 + 32)
    return round(celsius)

def from_display(value, unit):
    if unit == "F":
        return (value - 32) * 5 / 9
    return float(value)

# --- Display bounds (Celsius) ---
SETPOINT_MIN_C = -12
SETPOINT_MAX_C = 32

# --- Nextion helpers ---
def nextion_send(cmd: str):
    ser.write((cmd + "\xff\xff\xff").encode("iso-8859-1"))

def update_display():
    with state_lock:
        unit = state["unit"]
        d = to_display(state["discharge_temp"], unit)
        s = to_display(state["setpoint_temp"], unit)
    unit_label = "°F" if unit == "F" else "°C"
    nextion_send(f'n_discharge.val={d}')
    nextion_send(f'n_setpoint.val={s}')
    nextion_send(f'b_unit.txt="{unit_label}"')

def update_bounds_for_unit(unit: str):
    if unit == "F":
        sp_min = to_display(SETPOINT_MIN_C, "F")
        sp_max = to_display(SETPOINT_MAX_C, "F")
    else:
        sp_min, sp_max = SETPOINT_MIN_C, SETPOINT_MAX_C
    # Nextion Number components use minval/maxval to clamp input values.
    nextion_send(f"n_setpoint.minval={sp_min}")
    nextion_send(f"n_setpoint.maxval={sp_max}")

# --- Called by your sensor/control loop ---
def set_discharge_temp(celsius: float):
    """Write discharge temp from Python (e.g. from a sensor read)."""
    with state_lock:
        state["discharge_temp"] = celsius
    update_display()

def set_setpoint(celsius: float, source="python"):
    """Update setpoint from any source. Last write wins. Rounds to integer."""
    with state_lock:
        state["setpoint_temp"] = max(SETPOINT_MIN_C, min(SETPOINT_MAX_C, float(celsius)))
    update_display()
    print(f"[{source}] Setpoint → {state['setpoint_temp']}°C")

# --- Serial event parser ---
def parse_hmi_event(data: bytes):
    if not data:
        return
    print(f"[RAW] {data.hex()}")
    cmd = data[0]

    if cmd == 0x71 and len(data) == 5:
        raw_val = int.from_bytes(data[1:5], byteorder='little', signed=True)
        print(f"[HMI] n_setpoint.val = {raw_val}")
        with state_lock:
            celsius = from_display(raw_val, state["unit"])
            # Clamp to bounds
            celsius = max(SETPOINT_MIN_C, min(SETPOINT_MAX_C, celsius))
            state["setpoint_temp"] = celsius
            already_synced = state["synced"]
            state["synced"] = True
            unit = state["unit"]
        if already_synced:
            # Push clamped value back to HMI so it stays in sync
            s = to_display(celsius, unit)
            nextion_send(f'n_setpoint.val={s}')
            update_display()

    else:
        msg = data.replace(b'\xff', b'').decode("iso-8859-1", errors="ignore").strip()
        if msg == "U":
            with state_lock:
                state["unit"] = "F" if state["unit"] == "C" else "C"
                new_unit = state["unit"]
                s = to_display(state["setpoint_temp"], new_unit)
            nextion_send(f'n_setpoint.val={s}')
            print(f"[HMI] Unit toggled → {new_unit}")
            update_bounds_for_unit(new_unit)
            update_display()
        elif msg:
            print(f"[HMI] Unknown event: {msg!r}")

# --- Serial read loop ---
def serial_read_loop():
    buf = b""
    while True:
        chunk = ser.read(ser.in_waiting or 1)
        buf += chunk
        while b'\xff\xff\xff' in buf:
            packet, buf = buf.split(b'\xff\xff\xff', 1)
            parse_hmi_event(packet)

# --- Startup ---
def start(port=SERIAL_PORT, baud=BAUD_RATE):
    global ser
    ser = serial.Serial(port, baud, timeout=0.1)
    t = threading.Thread(target=serial_read_loop, daemon=True)
    t.start()
    time.sleep(1.0)
    print("[HMI] Sending get n_setpoint.val...")
    nextion_send('get n_setpoint.val')
    timeout = 3.0
    waited = 0.0
    while waited < timeout:
        with state_lock:
            if state["synced"]:
                break
        time.sleep(0.1)
        waited += 0.1
    if waited >= timeout:
        print("[HMI] Warning: n_setpoint.val sync timed out, using default")
    update_bounds_for_unit(state["unit"])
    update_display()
    print(f"[HMI] Connected on {port}")

if __name__ == "__main__":
    start()
    while True:
        pass
