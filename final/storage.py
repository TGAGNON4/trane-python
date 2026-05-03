"""On-disk persistence for temps, pressures, and setpoints.

Layout:
    BASE_DIR/DD-MM-YYYY/
        temps       — CSV row per sample
        pressures   — CSV row per sample
        setpoints   — HH:MM:SS,<celsius>  per setpoint change
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

from config import TZ, STORAGE_PURGE_THRESHOLD
from models import PressureRow, TempRow


def today_str() -> str:
    return datetime.now(TZ).strftime("%d-%m-%Y")


def dated_temps_file(base_dir: Path, date_str: str) -> Path:
    return base_dir / date_str / "temps"


def setpoints_file(base_dir: Path, date_str: str) -> Path:
    return base_dir / date_str / "setpoints"


def pressures_file(base_dir: Path, date_str: str) -> Path:
    return base_dir / date_str / "pressures"


def read_temps_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def append_setpoint(base_dir: Path, date_str: str, value: float) -> None:
    folder = base_dir / date_str
    folder.mkdir(parents=True, exist_ok=True)
    path = setpoints_file(base_dir, date_str)
    now_time = datetime.now(TZ).strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{now_time},{value}\n")


def load_setpoints(base_dir: Path, date_str: str) -> list[tuple[str, float]]:
    path = setpoints_file(base_dir, date_str)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",", 1)
        if len(parts) != 2:
            continue
        try:
            rows.append((parts[0].strip(), float(parts[1].strip())))
        except ValueError:
            continue
    return rows


def load_pressures(base_dir: Path, date_str: str) -> dict[str, PressureRow]:
    path = pressures_file(base_dir, date_str)
    if not path.exists():
        return {}
    rows: dict[str, PressureRow] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = parse_pressure_line(line)
        except ValueError:
            continue
        rows[row.timestamp] = row
    return rows


def parse_line(raw: str) -> TempRow:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 10:
        raise ValueError(f"expected 10 columns, got {len(parts)}")
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
        space_temp=float(parts[9]),
    )


def parse_pressure_line(raw: str) -> PressureRow:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 5:
        raise ValueError(f"expected 5 columns, got {len(parts)}")
    return PressureRow(
        timestamp=parts[0],
        high=float(parts[1]),
        low=float(parts[2]),
        evaporator=float(parts[3]),
        exv=float(parts[4]),
    )


def append_lines(path: Path, lines: list[str]) -> None:
    """Append lines and fsync to survive power loss."""
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())


def flush_buffers(base_dir: Path, date_str: str, temps: list[str], pressures: list[str]) -> None:
    append_lines(dated_temps_file(base_dir, date_str), temps)
    append_lines(pressures_file(base_dir, date_str), pressures)


def available_dates(base_dir: Path) -> list[str]:
    if not base_dir.exists():
        return []
    dates = []
    for item in sorted(base_dir.iterdir()):
        if item.is_dir() and (item / "temps").exists():
            dates.append(item.name)
    return dates


def time_range_for_date(base_dir: Path, date_str: str) -> str:
    temps_path = dated_temps_file(base_dir, date_str)
    if not temps_path.exists():
        return ""
    first = ""
    last = ""
    for line in temps_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ts = line.split(",", 1)[0].strip()
        if not first:
            first = ts
        last = ts
    if not first or not last:
        return ""
    return f"{first}-{last}"


def parse_requested_time(raw: str) -> tuple[str | None, str | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, None, None
    parts = raw.split()
    if len(parts) == 1:
        return None, None, parts[0].strip()
    if len(parts) == 2:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), None, parts[1].strip()
        return None, parts[0].strip(), parts[1].strip()
    if len(parts) >= 3:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), parts[1].strip(), parts[2].strip()
        return None, parts[0].strip(), parts[1].strip()
    return None, None, None


def purge_old_data_if_needed(base_dir: Path, threshold: float = STORAGE_PURGE_THRESHOLD) -> list[str]:
    """Delete oldest date folders (excluding today) until disk usage is below threshold."""
    usage = shutil.disk_usage(base_dir if base_dir.exists() else base_dir.parent)
    if usage.used / usage.total < threshold:
        return []

    today = today_str()
    dates = [d for d in available_dates(base_dir) if d != today]
    deleted = []
    for date_str in dates:
        usage = shutil.disk_usage(base_dir)
        if usage.used / usage.total < threshold:
            break
        folder = base_dir / date_str
        try:
            shutil.rmtree(folder)
            deleted.append(date_str)
            print(f"[storage] Deleted old data folder: {date_str} (disk >{threshold:.0%})")
        except OSError as exc:
            print(f"[storage] Failed to delete {date_str}: {exc}")
    return deleted


def parse_requested_range(raw: str) -> tuple[str | None, str | None, str | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, None, None, None
    parts = raw.split()
    if len(parts) == 2:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), None, parts[1].strip(), None
        return None, None, parts[0].strip(), parts[1].strip()
    if len(parts) == 3:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), None, parts[1].strip(), parts[2].strip()
        return None, parts[0].strip(), parts[1].strip(), parts[2].strip()
    if len(parts) >= 4:
        if parts[0].startswith("Circuit"):
            return parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
        return None, parts[0].strip(), parts[1].strip(), parts[2].strip()
    return None, None, None, None
