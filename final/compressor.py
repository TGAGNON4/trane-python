"""Compressor controller: PID loop, PWM output, startup ramp, and manual override.

State lives on a single `Compressor` instance. The state machine moves through:

    idle ─[start()]─► startup ramp ─► startup hold ─► PID control
                                                       │
                                          [shutdown_to_min] ─► override-to-min
                                          [restart_sequence] ─► back to startup

PID output (0.0–1.0) maps onto [VFD_MIN_RPM, VFD_MAX_RPM]; outputs below
PID output floors at VFD_MIN_RPM. All RPM transitions are rate-limited
to MAX_RPM_PER_SEC. A manual override bypasses PID until cleared or until the
setpoint changes.
"""

from __future__ import annotations

import time

from config import (
    MAX_RPM_PER_SEC,
    PID_KD,
    PID_KI,
    PID_KP,
    PID_OUTPUT_LIMITS,
    PID_SAMPLE_TIME,
    PWM_DUTY_CYCLE,
    PWM_FREQUENCY_HZ,
    PWM_PIN,
    PWM_STATUS_INTERVAL,
    SPACE_SETPOINT,
    STARTUP_DURATION_SECONDS,
    STARTUP_ENABLED,
    STARTUP_RPM,
    VFD_FREQ_AT_MAX_SPEED,
    VFD_FREQ_AT_MIN_SPEED,
    VFD_FREQ_OFF,
    VFD_MAX_RPM,
    VFD_MIN_RPM,
    VFD_RPM_AT_MAX_SPEED,
    VFD_RPM_AT_MIN_SPEED,
    VFD_SHUTDOWN_RPM,
)

try:
    from simple_pid import PID
    PID_IMPORT_ERROR = None
except Exception as exc:
    PID = None
    PID_IMPORT_ERROR = exc

try:
    import RPi.GPIO as GPIO
    GPIO_IMPORT_ERROR = None
except Exception as exc:
    GPIO = None
    GPIO_IMPORT_ERROR = exc


def make_pid() -> "PID | None":
    """Configure a reverse-acting (cooling) PI controller. Gains are negated."""
    if PID is None:
        return None
    pid = PID(Kp=-PID_KP, Ki=-PID_KI, Kd=-PID_KD, setpoint=SPACE_SETPOINT)
    pid.sample_time = PID_SAMPLE_TIME
    pid.output_limits = PID_OUTPUT_LIMITS
    return pid


class Compressor:
    """Owns the PWM hardware and all compressor control state."""

    def __init__(self) -> None:
        self._pwm = None
        self._startup_start_time: float | None = None
        self._startup_complete = False
        self._startup_target_rpm: float | None = None
        self._startup_ramp_seconds: float | None = None
        self._startup_hold_seconds: float | None = None
        self._override_rpm: float | None = None
        self._last_applied_freq: int | None = None
        self._current_rpm: float = 0.0
        self._last_rpm_update_ts: float | None = None
        self._last_print_ts: float | None = None
        self._shutdown_mode: bool = False
        self._idle: bool = True

    # ----- lifecycle -----

    def init_pwm(self) -> None:
        if GPIO is None:
            print(f"GPIO import failed: {GPIO_IMPORT_ERROR}")
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(PWM_PIN, GPIO.OUT)
            self._pwm = GPIO.PWM(PWM_PIN, PWM_FREQUENCY_HZ)
            self._pwm.start(0)
            self._current_rpm = 0.0
            self._last_rpm_update_ts = time.time()
            self._last_applied_freq = 0
            print("PWM initialized; waiting for compressor start button press")
        except Exception as exc:
            print(f"PWM initialization failed: {exc}")
            self._pwm = None

    def cleanup(self) -> None:
        if self._pwm is not None:
            try:
                self._pwm.stop()
                self._pwm = None
            except Exception as exc:
                print(f"PWM cleanup error: {exc}")
        if GPIO is not None:
            try:
                GPIO.cleanup()
            except Exception as exc:
                print(f"GPIO cleanup error: {exc}")

    # ----- state transitions -----

    def start(self) -> None:
        """Begin the startup ramp-and-hold sequence."""
        self._idle = False
        if not STARTUP_ENABLED:
            self._startup_complete = True
            return
        target = float(STARTUP_RPM)
        self._startup_target_rpm = target
        self._startup_ramp_seconds = (target - VFD_RPM_AT_MIN_SPEED) / MAX_RPM_PER_SEC
        self._startup_hold_seconds = max(0.0, STARTUP_DURATION_SECONDS - self._startup_ramp_seconds)
        self._current_rpm = float(VFD_RPM_AT_MIN_SPEED)
        self._last_rpm_update_ts = time.time()
        self._startup_start_time = time.time()
        self._startup_complete = False
        print(
            f"Starting compressor startup sequence: target_rpm={self._startup_target_rpm} "
            f"ramp_seconds={self._startup_ramp_seconds:.1f} "
            f"hold_seconds={self._startup_hold_seconds}"
        )

    def shutdown_to_min(self) -> None:
        """Ramp down to VFD_RPM_AT_MIN_SPEED and hold (graceful shutdown)."""
        self._shutdown_mode = True
        self.set_override_rpm(VFD_SHUTDOWN_RPM)

    def restart_sequence(self) -> None:
        """Cancel shutdown/override and re-run the startup sequence."""
        self._shutdown_mode = False
        self._override_rpm = None
        self.start()

    def set_override_rpm(self, rpm: float | None) -> None:
        """Set or clear a manual RPM override. Pass None to clear."""
        if rpm is None:
            self._override_rpm = None
        else:
            self._override_rpm = max(VFD_RPM_AT_MIN_SPEED, min(VFD_MAX_RPM, float(rpm)))

    # ----- queries -----

    def is_startup_complete(self) -> bool:
        if not STARTUP_ENABLED or self._startup_complete:
            return True
        if self._startup_start_time is None:
            return False
        elapsed = time.time() - self._startup_start_time
        ramp = self._startup_ramp_seconds or (self._startup_target_rpm or float(STARTUP_RPM)) / MAX_RPM_PER_SEC
        hold = self._startup_hold_seconds or 30.0
        if elapsed >= (ramp + hold):
            self._startup_complete = True
            print("Compressor startup sequence complete, switching to PID control")
            return True
        return False

    def status(self) -> str:
        if self._idle:
            return "Off"
        if self._shutdown_mode:
            return "Shutting Down"
        if not self.is_startup_complete():
            return "Starting"
        return "Running"

    def get_override_rpm(self) -> float | None:
        return self._override_rpm

    def get_current_rpm(self) -> float | None:
        return self._current_rpm

    def startup_status_line(self) -> str | None:
        if self._startup_complete or not STARTUP_ENABLED or self._startup_start_time is None:
            return None
        elapsed = time.time() - self._startup_start_time
        ramp = self._startup_ramp_seconds or 0.0
        hold = self._startup_hold_seconds or 30.0
        target = self._startup_target_rpm or float(STARTUP_RPM)
        freq = self._startup_rpm_to_freq(self._current_rpm)
        if elapsed <= ramp:
            pct = min(100.0, (elapsed / ramp * 100)) if ramp > 0 else 100.0
            return f"ramp {pct:.0f}% | {self._current_rpm:.0f}/{target:.0f} RPM | {freq} Hz"
        remaining = max(0.0, ramp + hold - elapsed)
        return f"hold {remaining:.0f}s left | {self._current_rpm:.0f}/{target:.0f} RPM | {freq} Hz"

    def is_shutdown_mode(self) -> bool:
        return self._shutdown_mode

    def is_shutdown_ramping(self) -> bool:
        """True while in shutdown mode and RPM has not yet reached the minimum."""
        return self._shutdown_mode and abs(self._current_rpm - VFD_SHUTDOWN_RPM) > 50.0

    def override_status_line(self) -> str | None:
        if self._override_rpm is None:
            return None
        freq = self._rpm_to_freq(self._current_rpm)
        diff = self._override_rpm - self._current_rpm
        if abs(diff) < 5.0:
            return f"{self._current_rpm:.0f} RPM | {freq} Hz"
        direction = "↑" if diff > 0 else "↓"
        return f"{direction} {self._current_rpm:.0f} → {self._override_rpm:.0f} RPM | {freq} Hz"

    # ----- per-tick control -----

    def apply_control_output(self, output: float) -> None:
        """Apply a normalized PID output (0-1) as PWM frequency.

        During startup, follows the ramp+hold profile; afterwards maps the
        full [0.0, 1.0] range linearly onto [VFD_MIN_RPM, VFD_MAX_RPM].
        PID never turns the compressor off — only shutdown_to_min() does.
        """
        if self._pwm is None:
            return

        if self._idle:
            self._apply_freq(VFD_FREQ_OFF)
            return

        if not self.is_startup_complete():
            try:
                elapsed = 0.0 if self._startup_start_time is None else (time.time() - self._startup_start_time)
                target = self._startup_target_rpm or float(STARTUP_RPM)
                ramp = self._startup_ramp_seconds or (target / MAX_RPM_PER_SEC)
                if elapsed <= ramp:
                    frac = (elapsed / ramp) if ramp > 0 else 1.0
                    desired_rpm = VFD_RPM_AT_MIN_SPEED + frac * (target - VFD_RPM_AT_MIN_SPEED)
                else:
                    desired_rpm = target
                self._step_rpm(desired_rpm)
                self._apply_freq(self._startup_rpm_to_freq(self._current_rpm))
            except Exception as exc:
                print(f"PWM startup control error: {exc}")
            return

        try:
            output = max(0.0, min(1.0, output))
            target_rpm = VFD_MIN_RPM + output * (VFD_MAX_RPM - VFD_MIN_RPM)
            self._step_rpm(target_rpm)
            self._apply_freq(self._rpm_to_freq(self._current_rpm))
        except Exception as exc:
            print(f"PWM control error: {exc}")

    def apply_override_rpm(self) -> bool:
        """Step toward the override RPM and apply it. Returns False when not active.

        Startup is never interrupted; the override only takes effect afterwards.
        """
        if self._override_rpm is None or self._pwm is None or self._idle or not self.is_startup_complete():
            return False
        try:
            self._step_rpm(float(self._override_rpm))
            self._apply_freq(self._rpm_to_freq(self._current_rpm))
            return True
        except Exception as exc:
            print(f"PWM override control error: {exc}")
            return False

    # ----- internal helpers -----

    def _step_rpm(self, target: float) -> None:
        """Advance _current_rpm toward target, rate-limited by MAX_RPM_PER_SEC."""
        now = time.time()
        dt = (now - self._last_rpm_update_ts) if self._last_rpm_update_ts is not None else 0.0
        self._last_rpm_update_ts = now
        max_step = MAX_RPM_PER_SEC * dt
        diff = target - self._current_rpm
        if abs(diff) <= max_step:
            self._current_rpm = target
        else:
            self._current_rpm += max_step if diff > 0 else -max_step

    def _apply_freq(self, freq: int) -> None:
        if self._pwm is None:
            return
        now = time.time()
        if freq <= 0:
            self._pwm.stop()
            self._last_applied_freq = 0
        else:
            self._pwm.stop()
            self._pwm.ChangeFrequency(freq)
            self._pwm.start(PWM_DUTY_CYCLE)
            self._last_applied_freq = freq
        if self._last_print_ts is None or (now - self._last_print_ts) >= PWM_STATUS_INTERVAL:
            if self._last_applied_freq <= VFD_FREQ_OFF:
                rpm_label = f"<{VFD_MIN_RPM:.0f} (off)"
            elif self._last_applied_freq >= VFD_FREQ_AT_MAX_SPEED:
                rpm_label = f"{VFD_RPM_AT_MAX_SPEED:.0f}"
            else:
                rpm = VFD_RPM_AT_MIN_SPEED + (self._last_applied_freq - VFD_FREQ_AT_MIN_SPEED) / (VFD_FREQ_AT_MAX_SPEED - VFD_FREQ_AT_MIN_SPEED) * (VFD_RPM_AT_MAX_SPEED - VFD_RPM_AT_MIN_SPEED)
                rpm_label = f"{rpm:.0f}"
            print(f"PWM: {self._last_applied_freq} Hz -> {rpm_label} RPM")
            self._last_print_ts = now

    @staticmethod
    def _rpm_to_freq(rpm: float) -> int:
        """Map RPM to Hz across the normal operating range."""
        if rpm < VFD_RPM_AT_MIN_SPEED:
            return VFD_FREQ_OFF
        rpm = min(rpm, VFD_MAX_RPM)
        freq = VFD_FREQ_AT_MIN_SPEED + (rpm - VFD_RPM_AT_MIN_SPEED) / (VFD_RPM_AT_MAX_SPEED - VFD_RPM_AT_MIN_SPEED) * (VFD_FREQ_AT_MAX_SPEED - VFD_FREQ_AT_MIN_SPEED)
        return int(round(freq))

    @staticmethod
    def _startup_rpm_to_freq(rpm: float) -> int:
        """Map RPM to Hz for the startup ramp; ramp starts at min VFD speed, not zero."""
        if rpm >= VFD_RPM_AT_MAX_SPEED:
            return VFD_FREQ_AT_MAX_SPEED
        freq = VFD_FREQ_AT_MIN_SPEED + (rpm - VFD_RPM_AT_MIN_SPEED) / (VFD_RPM_AT_MAX_SPEED - VFD_RPM_AT_MIN_SPEED) * (VFD_FREQ_AT_MAX_SPEED - VFD_FREQ_AT_MIN_SPEED)
        return max(VFD_FREQ_AT_MIN_SPEED, int(round(freq)))
