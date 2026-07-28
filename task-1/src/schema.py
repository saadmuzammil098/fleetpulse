"""Pandera schema: the data contract for cleaned FleetPulse telemetry.

This is what "physically plausible sensor ranges" and "missing-reading
rules" (Task 1 spec) actually mean in code: anything that fails this schema
is not safe to hand to a model.
"""
import pandera.pandas as pa
from pandera.pandas import Column, Check, DataFrameSchema

from .clean import SENSOR_RANGES

cleaned_schema = DataFrameSchema(
    {
        "unit_id": Column(int, Check.gt(0), unique=True, nullable=False),
        "product_id": Column(str, Check.str_matches(r"^[LMH]\d{5}$"), nullable=False),
        "type": Column(str, Check.isin(["L", "M", "H"]), nullable=False),
        "air_temperature_k": Column(
            float, Check.in_range(*SENSOR_RANGES["air_temperature_k"]), nullable=False
        ),
        "process_temperature_k": Column(
            float, Check.in_range(*SENSOR_RANGES["process_temperature_k"]), nullable=False
        ),
        "rotational_speed_rpm": Column(
            int, Check.in_range(*SENSOR_RANGES["rotational_speed_rpm"]), nullable=False
        ),
        "torque_nm": Column(
            float, Check.in_range(*SENSOR_RANGES["torque_nm"]), nullable=False
        ),
        "tool_wear_min": Column(
            int, Check.in_range(*SENSOR_RANGES["tool_wear_min"]), nullable=False
        ),
        "machine_failure": Column(int, Check.isin([0, 1]), nullable=False),
        "twf": Column(int, Check.isin([0, 1]), nullable=False),
        "hdf": Column(int, Check.isin([0, 1]), nullable=False),
        "pwf": Column(int, Check.isin([0, 1]), nullable=False),
        "osf": Column(int, Check.isin([0, 1]), nullable=False),
        "rnf": Column(int, Check.isin([0, 1]), nullable=False),
    },
    strict=False,
    coerce=False,
)
