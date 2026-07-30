# Task 4 — Break It On Purpose

Six deliberate bad requests against a running `task-4` API
(`./run_api.sh`, port 8811), each fired with `curl`. All six return a
clean `422` with a structured error body — none reach `FeatureComputer`
or the model, and none crash the process.

## 1. Out-of-range sensor value (torque above the physical max)

```
torque_nm: 200.0   (schema max: 150.0, task-1/src/clean.py::SENSOR_RANGES)
-> 422 {"detail":[{"type":"less_than_equal","loc":["body","telemetry_window",0,"torque_nm"],
        "msg":"Input should be less than or equal to 150", "input":200.0,"ctx":{"le":150.0}}]}
```

## 2. Out-of-range sensor value (negative rotational speed)

```
rotational_speed_rpm: -50   (schema min: 0)
-> 422 {"detail":[{"type":"greater_than_equal", ... "msg":"Input should be greater than or equal to 0"}]}
```

## 3. Missing required field

```
telemetry reading with no torque_nm key at all (sensor dropout / upstream rename)
-> 422 {"detail":[{"type":"missing","loc":["body","telemetry_window",0,"torque_nm"],
        "msg":"Field required"}]}
```

## 4. Wrong type (string where a float is required)

```
air_temperature_k: "hot"
-> 422 {"detail":[{"type":"float_parsing", ...
        "msg":"Input should be a valid number, unable to parse string as a number"}]}
```

## 5. Invalid categorical value

```
type: "Z"   (only L/M/H are real machine types)
-> 422 {"detail":[{"type":"enum","loc":["body","type"],
        "msg":"Input should be 'L', 'M' or 'H'"}]}
```

## 6. Empty telemetry window

```
telemetry_window: []
-> 422 {"detail":[{"type":"too_short", ...
        "msg":"List should have at least 1 item after validation, not 0"}]}
```

## What this proves

Every one of these fails at the Pydantic boundary, before
`inference.build_feature_row()` ever runs — `FeatureComputer.compute()`
and the registered model never see a physically impossible or malformed
reading. Each rejection is also logged as a structured
`validation_rejected` JSON line (path + full Pydantic error list), so a
fleet-ops caller sending bad data shows up in logs as a rate of rejected
requests, not as a silent bad prediction or an opaque 500.

A valid request, by contrast, returns a real prediction:

```
POST /predict {"vehicle_id":"veh-042","type":"M","telemetry_window":[...]}
-> 200 {"vehicle_id":"veh-042","failure_probability":0.0158,
        "recommended_action":"monitor","model_name":"fleetpulse-component-failure",
        "model_version":"1","readings_used":3}
```
