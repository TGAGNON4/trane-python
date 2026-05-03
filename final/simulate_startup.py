#!/usr/bin/env python3
"""Simulate the compressor startup ramp+hold and print the RPM→Hz timeline.

Defaults mirror the values used in control.py at the time this was written:
    base RPM = 2000 (1000 Hz), target clamped to 2700-3600,
    ramp_seconds = max(30, (target - 2000) / 120), hold_seconds = 30.
"""
import argparse


def rpm_to_freq(rpm: float) -> int:
    """RPM → VFD drive frequency (Hz). <=2000 RPM = 300 Hz idle, >=4000 = 5500 Hz."""
    if rpm <= 2000:
        return 300
    if rpm >= 4000:
        return 5500
    return int(round(1000 + (rpm - 2000) / 2000.0 * 4500))


def simulate(target_rpm: float, step: float) -> list[tuple[float, float, int]]:
    base_rpm = 2000.0
    target = max(2700.0, min(3600.0, target_rpm))
    ramp_seconds = max(30.0, max(0.0, target - base_rpm) / 120.0)
    hold_seconds = 30.0
    total = ramp_seconds + hold_seconds

    rows = []
    t = 0.0
    while t <= total + 1e-6:
        if t <= ramp_seconds:
            frac = (t / ramp_seconds) if ramp_seconds > 0 else 1.0
            desired_rpm = base_rpm + frac * (target - base_rpm)
        else:
            desired_rpm = target
        rows.append((round(t, 2), round(desired_rpm, 1), rpm_to_freq(desired_rpm)))
        t += step

    print(f"# target_rpm={target} ramp_seconds={ramp_seconds:.1f} hold_seconds={hold_seconds:.1f}")
    print("time_s,desired_rpm,pwm_freq_hz")
    for r in rows:
        print(f"{r[0]:.2f},{r[1]:.1f},{r[2]}")
    return rows


def write_csv(rows, path: str) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "desired_rpm", "pwm_freq_hz"])
        for r in rows:
            w.writerow(r)
    print(f"Wrote CSV to {path}")


def write_xlsx(rows, path: str) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["time_s", "desired_rpm", "pwm_freq_hz"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    print(f"Wrote Excel workbook to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate compressor startup ramp timeline")
    parser.add_argument("--target", type=float, default=3000.0, help="Target RPM (clamped 2700-3600)")
    parser.add_argument("--step", type=float, default=0.5, help="Time step in seconds")
    parser.add_argument("--out", type=str, default="", help="Optional output file (.csv or .xlsx)")
    args = parser.parse_args()

    rows = simulate(args.target, args.step)
    out = args.out.strip()
    if not out:
        pass
    elif out.lower().endswith(".xlsx"):
        try:
            write_xlsx(rows, out)
        except Exception:
            print("openpyxl not available; falling back to CSV output")
            write_csv(rows, out[:-5] + ".csv")
    elif out.lower().endswith(".csv"):
        write_csv(rows, out)
    else:
        write_csv(rows, out)
