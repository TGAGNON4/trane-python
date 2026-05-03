"""Entrypoint and orchestrator for the split data server.

`Application` owns sensors, MQTT, HMI, persistence, and the compressor —
every long-lived dependency the MQTT handlers need to read or mutate.
Lifecycle:  Application().run()  →  setup  →  sample loop  →  cleanup.
"""

from __future__ import annotations

import math
import socket
import time

import paho.mqtt.client as mqtt

from compressor import Compressor, make_pid
from config import (
    BASE_DIR,
    BROKER,
    CIRCUIT,
    HMI_BAUD,
    HMI_ENABLED,
    HMI_PORT,
    HMI_SETPOINT_MAX_C,
    HMI_SETPOINT_MIN_C,
    PASSWORD,
    PID_ENABLED,
    PORT,
    PWM_ENABLED,
    RAM_BUFFER_MAX_BYTES,
    RAM_BUFFER_MAX_SECONDS,
    RUN_FOR_SECONDS,
    SAMPLE_SECONDS,
    SPACE_SETPOINT,
    STORAGE_PURGE_CHECK_INTERVAL,
    USERNAME,
)
from hmi import HMIController, SERIAL_IMPORT_ERROR, serial
from mqtt_handlers import attach
from mqtt_publish import data_topic, publish_row
from sensors import SENSOR_IMPORT_ERROR, init_i2c_devices, read_live_line
from storage import (
    append_setpoint,
    flush_buffers,
    parse_line,
    purge_old_data_if_needed,
    today_str,
)


class Application:
    def __init__(self) -> None:
        self.client: mqtt.Client | None = None
        self.compressor = Compressor()
        self.pid = None
        self.hmi: HMIController | None = None
        self.i2c_devices = None

        self.current_setpoint: dict[str, float] = {CIRCUIT: SPACE_SETPOINT}
        self.display_unit: str = "C"
        self._hmi_display_state: str = ""  # tracks what is currently shown on the HMI

        self._temp_buffer: list[str] = []
        self._pressure_buffer: list[str] = []
        self._buffer_bytes: int = 0
        self._buffer_start_ts: float | None = None
        self._buffer_date: str = today_str()

    # ----- top-level -----

    def run(self) -> None:
        if not self._setup():
            return
        attach(self)
        self._connect_mqtt()
        self.client.loop_start()
        try:
            self._sample_loop()
        except KeyboardInterrupt:
            print("\nStopping data server...")
        finally:
            self._cleanup()

    # ----- setup -----

    def _setup(self) -> bool:
        print(f"Starting data server for {CIRCUIT}")
        print(f"Base directory: {BASE_DIR}")
        print(f"MQTT broker: {BROKER}:{PORT}")
        print(f"Sample rate: {SAMPLE_SECONDS}s")

        if not BASE_DIR.exists():
            try:
                BASE_DIR.mkdir(parents=True, exist_ok=True)
                print(f"Created base dir: {BASE_DIR}")
            except OSError as exc:
                print(f"Base dir not found and could not be created: {BASE_DIR} ({exc})")

        self.i2c_devices = init_i2c_devices()
        if self.i2c_devices is None:
            if SENSOR_IMPORT_ERROR:
                print(f"Sensor import error: {SENSOR_IMPORT_ERROR}")
            else:
                print("Live mode needs board/busio + adafruit_mlx90614 + adafruit_ads1x15.")
                print("Also verify I2C is enabled and sensors are visible on the bus.")
            return False

        self.pid = make_pid()
        if PWM_ENABLED:
            self.compressor.init_pwm()

        if HMI_ENABLED:
            if serial is None:
                print(f"HMI serial unavailable: {SERIAL_IMPORT_ERROR}")
            else:
                hmi = HMIController(HMI_PORT, HMI_BAUD)
                if hmi.start():
                    hmi.set_setpoint(self.current_setpoint[CIRCUIT])
                    self.hmi = hmi
                    self._update_hmi_display_state()
                    print(f"HMI connected on {HMI_PORT}")
                else:
                    print(f"HMI failed to open {HMI_PORT}")

        self.client = mqtt.Client(
            protocol=mqtt.MQTTv5,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self.client.username_pw_set(USERNAME, PASSWORD)
        return True

    def _connect_mqtt(self) -> None:
        while True:
            try:
                self.client.connect(BROKER, PORT, keepalive=60)
                return
            except socket.gaierror as exc:
                print(f"DNS lookup failed for MQTT broker '{BROKER}': {exc}. Retrying in 5s...")
                time.sleep(5)
            except OSError as exc:
                print(f"MQTT connection failed to {BROKER}:{PORT}: {exc}. Retrying in 5s...")
                time.sleep(5)

    # ----- shared mutators (called from both MQTT and HMI paths) -----

    def apply_setpoint(self, value: float, publish_mqtt: bool, update_hmi: bool) -> None:
        """Central setpoint handler — clamps, persists, retunes PID, fans out updates."""
        clamped = max(HMI_SETPOINT_MIN_C, min(HMI_SETPOINT_MAX_C, float(value)))
        append_setpoint(BASE_DIR, today_str(), clamped)
        self.current_setpoint[CIRCUIT] = clamped
        if self.pid is not None:
            self.pid.setpoint = clamped
        if self.compressor.get_override_rpm() is not None:
            self.compressor.set_override_rpm(None)
            self.client.publish(f"{CIRCUIT}/Compressor_RPM", "", qos=0, retain=True)
            print("RPM override cleared by setpoint change")
        if update_hmi and self.hmi is not None:
            self.hmi.request_display_setpoint(clamped)
        if publish_mqtt:
            self.client.publish(
                f"{CIRCUIT}/Space_Setpoint_Temperature",
                f"{clamped}",
                qos=0,
                retain=True,
            )
            self.client.publish(
                data_topic(CIRCUIT, "Setpoint_Record"),
                f"{CIRCUIT} {clamped}",
                qos=0,
                retain=False,
            )

    def request_shutdown(self, source: str) -> None:
        self.compressor.shutdown_to_min()
        self.client.publish(data_topic(CIRCUIT, "Compressor_Shutdown_Status"), "ramping_to_min", qos=0, retain=True)
        self.client.publish(f"{CIRCUIT}/Status", "Shutting Down", qos=0, retain=True)
        print(f"Compressor shutdown requested via {source} — ramping to min RPM")

    def request_start(self, source: str) -> None:
        self.compressor.restart_sequence()
        self.client.publish(f"{CIRCUIT}/Status", "Starting", qos=0, retain=True)
        print(f"Compressor start requested via {source} — restarting startup sequence")

    def set_unit(self, unit: str, push_to_hmi: bool) -> None:
        self.display_unit = unit
        if push_to_hmi and self.hmi is not None:
            self.hmi.set_unit(unit)
        self.client.publish(f"{CIRCUIT}/Unit", unit, qos=0, retain=True)

    # ----- HMI event handlers (registered with hmi.pump()) -----

    def _on_hmi_setpoint(self, value: float) -> None:
        print(f"[HMI] Setpoint change → {value:.1f}°C")
        self.apply_setpoint(value, publish_mqtt=True, update_hmi=False)

    def _on_hmi_shutdown(self) -> None:
        self.request_shutdown("HMI")

    def _on_hmi_startup(self) -> None:
        self.request_start("HMI")

    def _on_hmi_unit(self, unit: str) -> None:
        self.set_unit(unit, push_to_hmi=False)

    def _update_hmi_display_state(self) -> None:
        """Switch the HMI page to match the current compressor state."""
        if self.hmi is None:
            return
        status = self.compressor.status()
        if status == "Off":
            desired = "setup"
        elif status == "Starting":
            desired = "ramp_up"
        elif status == "Shutting Down" and self.compressor.is_shutdown_ramping():
            desired = "ramp_down"
        else:
            desired = "normal"

        if desired == self._hmi_display_state:
            return
        self._hmi_display_state = desired
        if desired == "setup":
            self.hmi.show_status("Setting up ...")
        elif desired == "ramp_up":
            self.hmi.show_status("Ramping RPM up ...")
        elif desired == "ramp_down":
            self.hmi.show_status("Ramping RPM down ...")
        else:
            self.hmi.show_normal()

    # ----- main loop -----

    def _sample_loop(self) -> None:
        print("Starting live data collection...")
        start_ts = time.time()
        last_status_print_ts = time.time()
        sample_count = 0

        while True:
            if RUN_FOR_SECONDS is not None and (time.time() - start_ts) >= RUN_FOR_SECONDS:
                print(f"Reached run limit ({RUN_FOR_SECONDS}s). Stopping data server...")
                return

            temp_line, pressure_line, pressure, line_date = read_live_line(*self.i2c_devices)
            sample_count += 1
            if sample_count % STORAGE_PURGE_CHECK_INTERVAL == 0:
                purge_old_data_if_needed(BASE_DIR)

            self._handle_hmi_tick()
            self._buffer_sample(temp_line, pressure_line, line_date)

            try:
                row = parse_line(temp_line)
                publish_row(
                    self.client,
                    CIRCUIT,
                    row,
                    self.current_setpoint.get(CIRCUIT, SPACE_SETPOINT),
                    int(time.time() * 1000),
                    pressure,
                )
                self.client.publish(f"{CIRCUIT}/Status", self.compressor.status(), qos=0, retain=True)
                if PID_ENABLED and self.pid is not None and not self.compressor.apply_override_rpm():
                    if math.isnan(row.evaporator_ambient):
                        control_output = 0.0
                    else:
                        control_output = self.pid(row.evaporator_ambient)
                    self.compressor.apply_control_output(control_output)
                current_rpm = self.compressor.get_current_rpm()
                if current_rpm is not None:
                    self.client.publish(f"{CIRCUIT}/Compressor_Current_RPM", f"{current_rpm:.0f}", qos=0, retain=True)
            except ValueError:
                pass

            self._maybe_flush_buffers()

            if time.time() - last_status_print_ts >= 5.0:
                startup_msg = self.compressor.startup_status_line()
                override_msg = self.compressor.override_status_line()
                if startup_msg:
                    print(f"[STARTUP] {startup_msg}")
                elif override_msg:
                    print(f"[OVERRIDE] {override_msg}")
                last_status_print_ts = time.time()

            time.sleep(SAMPLE_SECONDS)

    # ----- per-tick helpers -----

    def _handle_hmi_tick(self) -> None:
        if self.hmi is None:
            return
        if not self.hmi.is_connected():
            print("[HMI] Disconnected; running without HMI")
            self.client.publish(f"{CIRCUIT}/HMI_Status", "0", qos=0, retain=True)
            self.hmi = None
            return
        self.hmi.pump(
            on_setpoint=self._on_hmi_setpoint,
            on_shutdown=self._on_hmi_shutdown,
            on_startup=self._on_hmi_startup,
            on_unit=self._on_hmi_unit,
        )
        self._update_hmi_display_state()

    def _buffer_sample(self, temp_line: str, pressure_line: str, line_date: str) -> None:
        if line_date != self._buffer_date and (self._temp_buffer or self._pressure_buffer):
            flush_buffers(BASE_DIR, self._buffer_date, self._temp_buffer, self._pressure_buffer)
            self._reset_buffers()
            self._buffer_date = line_date
        elif line_date != self._buffer_date:
            self._buffer_date = line_date

        self._temp_buffer.append(temp_line)
        self._pressure_buffer.append(pressure_line)
        self._buffer_bytes += len(temp_line) + len(pressure_line) + 2
        if self._buffer_start_ts is None:
            self._buffer_start_ts = time.time()

    def _maybe_flush_buffers(self) -> None:
        now_ts = time.time()
        too_big = self._buffer_bytes >= RAM_BUFFER_MAX_BYTES
        too_old = self._buffer_start_ts is not None and now_ts - self._buffer_start_ts >= RAM_BUFFER_MAX_SECONDS
        if too_big or too_old:
            flush_buffers(BASE_DIR, self._buffer_date, self._temp_buffer, self._pressure_buffer)
            self._reset_buffers()

    def _reset_buffers(self) -> None:
        self._temp_buffer = []
        self._pressure_buffer = []
        self._buffer_bytes = 0
        self._buffer_start_ts = None

    # ----- shutdown -----

    def _cleanup(self) -> None:
        if self._temp_buffer or self._pressure_buffer:
            flush_buffers(BASE_DIR, self._buffer_date, self._temp_buffer, self._pressure_buffer)
        if PWM_ENABLED:
            self.compressor.cleanup()
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()


def main() -> None:
    Application().run()


if __name__ == "__main__":
    main()
