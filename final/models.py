"""Sample dataclasses shared by sensor reads, CSV storage, and MQTT publishing."""

from dataclasses import dataclass


@dataclass
class TempRow:
    """MLX90614 readings in °C. evaporator_ambient = discharge air (PID input)."""
    timestamp: str
    high_ambient: float
    high_object: float
    low_ambient: float
    low_object: float
    evaporator_ambient: float
    evaporator_object: float
    exv_ambient: float
    exv_object: float
    space_temp: float  # mean of the four ambients


@dataclass
class PressureRow:
    """Pressure readings in Pascals."""
    timestamp: str
    high: float
    low: float
    evaporator: float
    exv: float
