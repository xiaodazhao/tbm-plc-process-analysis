# Optional Last-Layer Polishing Pilot

This experiment uses the two existing 2023-12-20 and 2024-01-24 candidate reports.
It does not alter the production pipeline, evidence, original reports or analyses.

The model returns paragraph replacements and its own HTML/CSS. The program
preserves claim metadata, validates the existing facts and numbers, validates
visible placeholders, inserts unchanged tables and figures, and prints to PDF.
Actual PDF font size is checked to catch browser shrink-to-fit. Full semantic
equivalence and writing quality still require human comparison.

Offline tests, from the project root:

```sh
python -m pytest -q experiments/report_polishing_pilot_20261028/test_pilot.py
```

The `prepare` stage requires a new output directory. `generate` and
`visual-feedback` use the existing official DeepSeek interface and configured
local key. They are paid operations, not offline reproduction commands. The
pilot allows one initial and one feedback call for each date. Existing call
logs and stage directories prevent unnoticed reruns. `deliver` copies both
versions and makes a hash-verified ZIP; no previous experiment is overwritten.

The snapshot in the deliverable is experiment code, not a standalone duplicate
of all production dependencies. It runs against the existing TBM project.
