"""Request/response contracts for the Fleet Health Risk API.

Sensor bounds mirror Task 1's data contract exactly
(``task-1/src/clean.py::SENSOR_RANGES``, enforced there by a pandera
schema on the training data) — the same physically plausible ranges the
model was trained inside. A reading outside them isn't a value the model
has ever seen a meaningful gradient for; rejecting it at the API boundary,
before it reaches ``FeatureComputer`` or the model, is cheaper and clearer
than letting the model silently extrapolate.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# Same bounds as task-1/src/clean.py::SENSOR_RANGES — the training-data
# contract. Kept as literal values, not a cross-package import, for the
# same "src" package-name collision reason documented in
# shared_features.py; these five numbers are copied once, deliberately,
# and would need to move in lockstep if Task 1's contract ever changes.
SENSOR_RANGES: dict[str, tuple[float, float]] = {
    "air_temperature_k": (280.0, 320.0),
    "process_temperature_k": (285.0, 330.0),
    "rotational_speed_rpm": (0.0, 5000.0),
    "torque_nm": (0.0, 150.0),
    "tool_wear_min": (0.0, 400.0),
}


class MachineType(str, Enum):
    L = "L"
    M = "M"
    H = "H"


class SensorReading(BaseModel):
    """One raw telemetry frame off a fleet vehicle."""

    air_temperature_k: float = Field(
        ..., ge=SENSOR_RANGES["air_temperature_k"][0], le=SENSOR_RANGES["air_temperature_k"][1],
        description="Ambient air temperature at the sensor, kelvin.",
    )
    process_temperature_k: float = Field(
        ..., ge=SENSOR_RANGES["process_temperature_k"][0], le=SENSOR_RANGES["process_temperature_k"][1],
        description="Process/component temperature, kelvin.",
    )
    rotational_speed_rpm: float = Field(
        ..., ge=SENSOR_RANGES["rotational_speed_rpm"][0], le=SENSOR_RANGES["rotational_speed_rpm"][1],
        description="Spindle/shaft rotational speed, rpm.",
    )
    torque_nm: float = Field(
        ..., ge=SENSOR_RANGES["torque_nm"][0], le=SENSOR_RANGES["torque_nm"][1],
        description="Applied torque, newton-meters.",
    )
    tool_wear_min: float = Field(
        ..., ge=SENSOR_RANGES["tool_wear_min"][0], le=SENSOR_RANGES["tool_wear_min"][1],
        description="Elapsed tool-wear minutes since last service.",
    )
    recorded_at: datetime | None = Field(
        default=None, description="Optional client-side timestamp for this reading."
    )


class PredictRequest(BaseModel):
    """A vehicle's recent telemetry window, oldest reading first.

    The window is replayed through a fresh ``FeatureComputer`` in arrival
    order so the most recent reading's rolling-window features (moving
    averages, rate-of-change) are computed the same way training computed
    them — see ``inference.py``. At least one reading is required; more
    readings warm the rolling window up further, the same way a longer-
    running live stream would.
    """

    vehicle_id: str = Field(..., min_length=1, description="Fleet vehicle identifier.")
    type: MachineType = Field(..., description="Machine type (L/M/H) — the model's stream key.")
    telemetry_window: list[SensorReading] = Field(
        ..., min_length=1, max_length=500,
        description="Recent readings for this vehicle, oldest first.",
    )

    @field_validator("telemetry_window")
    @classmethod
    def _non_empty(cls, v: list[SensorReading]) -> list[SensorReading]:
        if not v:
            raise ValueError("telemetry_window must contain at least one reading")
        return v


class RecommendedAction(str, Enum):
    MONITOR = "monitor"
    SCHEDULE_SERVICE = "schedule_service"
    URGENT_ALERT = "urgent_alert"


class PredictResponse(BaseModel):
    vehicle_id: str
    failure_probability: float = Field(..., ge=0.0, le=1.0)
    recommended_action: RecommendedAction
    model_name: str
    model_version: str
    readings_used: int


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_name: str | None = None
    model_version: str | None = None
