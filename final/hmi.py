"""Nextion HMI serial controller.

Inbound packets from the panel are parsed off a daemon thread and pushed
onto internal queues. The application drains them once per sample tick by
calling `pump()` with per-event callbacks.

Outbound display updates can be requested from any thread via
`request_display_setpoint()`; the next `pump()` call applies them.

Packet legend:
    0x71 + i32  → setpoint   (committed n_setpoint value)
    "U"         → unit       ('C' or 'F' after toggle)
    "S"         → shutdown
    "T"         → startup
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from config import (
    HMI_COMPONENT_SETPOINT,
    HMI_COMPONENT_UNIT,
    HMI_SETPOINT_MAX_C,
    HMI_SETPOINT_MIN_C,
)

try:
    import serial
    SERIAL_IMPORT_ERROR = None
except Exception as exc:
    serial = None
    SERIAL_IMPORT_ERROR = exc


SetpointHandler = Callable[[float], None]
EventHandler = Callable[[], None]
UnitHandler = Callable[[str], None]


class HMIController:
    TERMINATOR = b"\xff\xff\xff"

    def __init__(self, port: str, baud: int):
        self._port = port
        self._baud = baud
        self._lock = threading.Lock()
        self._ser: object | None = None
        self._reader: threading.Thread | None = None
        self._state = {
            "setpoint_temp": 5.0,
            "unit": "C",
            "synced": False,
        }
        self._setpoint_q: queue.Queue[float] = queue.Queue(maxsize=32)
        self._shutdown_q: queue.Queue[bool] = queue.Queue(maxsize=8)
        self._startup_q: queue.Queue[bool] = queue.Queue(maxsize=8)
        self._unit_q: queue.Queue[str] = queue.Queue(maxsize=8)
        self._display_q: queue.Queue[float] = queue.Queue(maxsize=1)

    # ----- lifecycle -----

    def start(self) -> bool:
        """Open the serial port and sync the current setpoint from the panel."""
        if serial is None:
            return False
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=0.1)
        except Exception:
            return False
        self._reader = threading.Thread(target=self._serial_read_loop, daemon=True)
        self._reader.start()
        time.sleep(1.0)
        self._nextion_send(f"get {HMI_COMPONENT_SETPOINT}.val")
        timeout = 3.0
        waited = 0.0
        while waited < timeout:
            with self._lock:
                if self._state["synced"]:
                    break
            time.sleep(0.1)
            waited += 0.1
        self._update_bounds_for_unit(self._state["unit"])
        self.update_display()
        return True

    def is_connected(self) -> bool:
        return self._ser is not None

    # ----- public outbound API -----

    def get_unit(self) -> str:
        with self._lock:
            return self._state["unit"]

    def set_unit(self, unit: str) -> None:
        with self._lock:
            self._state["unit"] = unit
            s = self._to_display(self._state["setpoint_temp"], unit)
        self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.val={s}")
        self._update_bounds_for_unit(unit)
        self.update_display()

    def set_setpoint(self, celsius: float) -> None:
        with self._lock:
            self._state["setpoint_temp"] = max(HMI_SETPOINT_MIN_C, min(HMI_SETPOINT_MAX_C, float(celsius)))
        self.update_display()

    def update_display(self) -> None:
        with self._lock:
            unit = self._state["unit"]
            s = self._to_display(self._state["setpoint_temp"], unit)
        unit_label = "°F" if unit == "F" else "°C"
        self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.val={s}")
        self._nextion_send(f'{HMI_COMPONENT_UNIT}.txt="{unit_label}"')

    def request_display_setpoint(self, celsius: float) -> None:
        """Queue a setpoint to be pushed to the panel on the next `pump()`."""
        try:
            self._display_q.get_nowait()
        except queue.Empty:
            pass
        try:
            self._display_q.put_nowait(celsius)
        except queue.Full:
            pass

    # ----- per-tick event drain -----

    def pump(
        self,
        *,
        on_setpoint: SetpointHandler | None = None,
        on_shutdown: EventHandler | None = None,
        on_startup: EventHandler | None = None,
        on_unit: UnitHandler | None = None,
    ) -> None:
        """Drain inbound queues and flush any pending display update."""
        self._drain(self._setpoint_q, on_setpoint)
        self._drain(self._shutdown_q, lambda _v: on_shutdown() if on_shutdown else None)
        self._drain(self._startup_q, lambda _v: on_startup() if on_startup else None)
        self._drain(self._unit_q, on_unit)

        try:
            disp_sp = self._display_q.get_nowait()
        except queue.Empty:
            return
        self.set_setpoint(disp_sp)
        print(f"[HMI] Display updated → {disp_sp:.1f}°C (website)")

    @staticmethod
    def _drain(q: queue.Queue, handler) -> None:
        while True:
            try:
                value = q.get_nowait()
            except queue.Empty:
                return
            if handler is not None:
                handler(value)

    # ----- internal: serial I/O -----

    def _nextion_send(self, cmd: str) -> None:
        if self._ser is None:
            return
        with self._lock:
            if self._ser is None:
                return
            try:
                self._ser.write((cmd + "\xff\xff\xff").encode("iso-8859-1"))
            except Exception as exc:
                print(f"[HMI] Serial write error ({exc}); marking disconnected")
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    def _to_display(self, celsius: float, unit: str) -> int:
        if unit == "F":
            return round(celsius * 9 / 5 + 32)
        return round(celsius)

    def _from_display(self, value: int, unit: str) -> float:
        if unit == "F":
            return (value - 32) * 5 / 9
        return float(value)

    def _update_bounds_for_unit(self, unit: str) -> None:
        if unit == "F":
            sp_min = self._to_display(HMI_SETPOINT_MIN_C, "F")
            sp_max = self._to_display(HMI_SETPOINT_MAX_C, "F")
        else:
            sp_min, sp_max = int(HMI_SETPOINT_MIN_C), int(HMI_SETPOINT_MAX_C)
        self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.minval={sp_min}")
        self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.maxval={sp_max}")

    def _parse_hmi_event(self, data: bytes) -> None:
        if not data:
            return
        cmd = data[0]
        if cmd == 0x71 and len(data) == 5:
            raw_val = int.from_bytes(data[1:5], byteorder="little", signed=True)
            with self._lock:
                celsius = self._from_display(raw_val, self._state["unit"])
                celsius = max(HMI_SETPOINT_MIN_C, min(HMI_SETPOINT_MAX_C, celsius))
                self._state["setpoint_temp"] = celsius
                already_synced = self._state["synced"]
                self._state["synced"] = True
                unit = self._state["unit"]
            if already_synced:
                # Echo only the value; calling update_display() here floods the
                # port and can cause the panel to drop the next incoming packet.
                s = self._to_display(celsius, unit)
                self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.val={s}")
            try:
                self._setpoint_q.put_nowait(celsius)
            except queue.Full:
                pass
            return

        msg = data.replace(b"\xff", b"").decode("iso-8859-1", errors="ignore").strip()
        if msg == "U":
            with self._lock:
                self._state["unit"] = "F" if self._state["unit"] == "C" else "C"
                new_unit = self._state["unit"]
                s = self._to_display(self._state["setpoint_temp"], new_unit)
            self._nextion_send(f"{HMI_COMPONENT_SETPOINT}.val={s}")
            self._update_bounds_for_unit(new_unit)
            self.update_display()
            try:
                self._unit_q.put_nowait(new_unit)
            except queue.Full:
                pass
        elif msg == "S":
            try:
                self._shutdown_q.put_nowait(True)
            except queue.Full:
                pass
        elif msg == "T":
            try:
                self._startup_q.put_nowait(True)
            except queue.Full:
                pass

    def _serial_read_loop(self) -> None:
        if self._ser is None:
            return
        buf = b""
        while True:
            if self._ser is None:
                break
            try:
                chunk = self._ser.read(self._ser.in_waiting or 1)
            except Exception as exc:
                print(f"[HMI] Serial read error ({exc}); disconnecting")
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
                break
            if not chunk:
                continue
            buf += chunk
            while self.TERMINATOR in buf:
                packet, buf = buf.split(self.TERMINATOR, 1)
                self._parse_hmi_event(packet)
