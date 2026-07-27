# FleetPulse Day 1 — Break-It Demo

10-row synthetic fixture (the real AI4I 2020 data has zero faults, so it never exercises this code): 6 rows each carry one isolated, deliberate fault (missing value, impossible temperature, negative speed, absurd tool wear, invalid category, bad timestamp string), the remaining 4 rows are an untouched control group. Goal: every corrupted row gets caught somewhere in the pipeline; every clean row survives all the way to a passing validation.

## Input (as ingested)

|   unit_id | product_id   | type   |   air_temperature_k |   process_temperature_k |   rotational_speed_rpm |   torque_nm |   tool_wear_min |   machine_failure |   twf |   hdf |   pwf |   osf |   rnf |
|----------:|:-------------|:-------|--------------------:|------------------------:|-----------------------:|------------:|----------------:|------------------:|------:|------:|------:|------:|------:|
|         1 | M14860       | M      |               298.1 |                   308.6 |                   1551 |       nan   |               0 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         2 | L47181       | L      |               999.9 |                   308.7 |                   1408 |        46.3 |               3 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         3 | L47182       | L      |               298.1 |                   308.5 |                   -500 |        49.4 |               5 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         4 | L47183       | L      |               298.2 |                   308.6 |                   1433 |        39.5 |           99999 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         5 | M14861       | X      |               298.3 |                   308.7 |                   1500 |        40.1 |              10 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         6 | H29424       | H      |               298.1 |                   308.6 |                   1520 |        41   |              12 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         7 | L47184       | L      |               298.2 |                   308.7 |                   1490 |        43.2 |              15 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         8 | M14862       | M      |               298.3 |                   308.8 |                   1510 |        44   |              18 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         9 | L47185       | L      |               298.2 |                   308.6 |                   1505 |        42   |              20 |                 0 |     0 |     0 |     0 |     0 |     0 |
|        10 | H29425       | H      |               298.1 |                   308.7 |                   1495 |        41.5 |              22 |                 0 |     0 |     0 |     0 |     0 |     0 |

Timestamp column (not part of the real dataset, injected here only to exercise the timestamp cleaner): ['2026-01-01T00:00:00', '2026-01-01T00:05:00', '2026-01-01T00:10:00', '2026-01-01T00:15:00', '2026-01-01T00:20:00', 'not-a-timestamp', '2026-01-01T00:30:00', '2026-01-01T00:35:00', '2026-01-01T00:40:00', '2026-01-01T00:45:00']

## Timestamp cleaning

Dropped 1 row(s) with an unparseable timestamp (the row with the literal string `"not-a-timestamp"`).

## Missing-value / outlier cleaning

- rows in: 9
- dropped for missing required field: 1 (null counts: {'torque_nm': 1})
- dropped for out-of-range sensor reading: 3 (by column: {'air_temperature_k': 1, 'process_temperature_k': 0, 'rotational_speed_rpm': 1, 'torque_nm': 0, 'tool_wear_min': 1})
- rows out: 5

## Surviving rows

|   unit_id | product_id   | type   |   air_temperature_k |   process_temperature_k |   rotational_speed_rpm |   torque_nm |   tool_wear_min |   machine_failure |   twf |   hdf |   pwf |   osf |   rnf |
|----------:|:-------------|:-------|--------------------:|------------------------:|-----------------------:|------------:|----------------:|------------------:|------:|------:|------:|------:|------:|
|         5 | M14861       | X      |               298.3 |                   308.7 |                   1500 |        40.1 |              10 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         7 | L47184       | L      |               298.2 |                   308.7 |                   1490 |        43.2 |              15 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         8 | M14862       | M      |               298.3 |                   308.8 |                   1510 |        44   |              18 |                 0 |     0 |     0 |     0 |     0 |     0 |
|         9 | L47185       | L      |               298.2 |                   308.6 |                   1505 |        42   |              20 |                 0 |     0 |     0 |     0 |     0 |     0 |
|        10 | H29425       | H      |               298.1 |                   308.7 |                   1495 |        41.5 |              22 |                 0 |     0 |     0 |     0 |     0 |     0 |

## Final schema validation on survivors

Result: FAILED

Failing cases: 1

## What this proves

Started with 10 rows: 1 missing value, 1 impossible temperature, 1 negative speed, 1 absurd tool wear, 1 invalid category, 1 bad timestamp, 4 untouched controls. 5 row(s) survived cleaning. The missing-value, out-of-range-sensor, and bad-timestamp faults are all caught and dropped during **cleaning**, before validation ever runs. The invalid-category fault is a different kind of problem, structurally present and not an outlier, so cleaning correctly leaves it alone (it's present in the surviving rows above), and it's the **schema validator** that catches it: final validation FAILED on the cleaning survivors, which is exactly the point of having two layers, cleaning handles bad *values*, the schema validator enforces the data *contract*, and between them nothing corrupt reaches the cleaned output the pipeline actually writes.