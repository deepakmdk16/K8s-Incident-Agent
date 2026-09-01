# Evals — deterministic verification of every reported number

**Deterministic core** (`run.sh`): **evaluation cases we design ourselves**,
under the pre-registered design rules in
[`docs/decisions/problem-selection.md`](../docs/decisions/problem-selection.md):
one primary metric meaningful to the user, **≥10 cases**, **one deliberately
challenging case**, the **same cases** for `baseline/` and `solution/`, and a
written definition of "good" *before* the first run. Output: the comparison
table pinned in `evals/reported.json` (root-cause identification overall and
per tier, resource identification, calibration; rules / baseline / solution
columns) — the measured-improvement evidence. Cost per arm is disclosed from
each bundle's `summary.json`; human time per task is deliberately not
reported — no human trial was run (`evals/scoring.md`). Wired into
`scripts/checkpoints.sh`: a red eval fails the gate, and `run.sh` must be
`chmod +x` or the gate silently skips it.

**Scored runs** (`uv run python -m evals.run_eval --arm baseline`): one arm ×
all cases × N replicate runs, scored by the frozen `scoring.py`; the evidence
bundle (per-case prompt/report/answer/metrics, rows.jsonl, summary.json/md)
lands in `evals/results/<stamp>-<arm>/` and is **committed** — every README or
CHANGELOG number cites one of these bundles. `evals/out/` stays for ad-hoc
artifacts. Scored runs hit the live API (key required) and are invoked
explicitly, never from checkpoints — the gate must stay offline-deterministic;
`run.sh` re-verifies the committed bundles offline instead of re-running the
LLM.

What `run.sh` does (offline by construction — no API calls, no cluster, ~1 s):

- Execs `uv run python -m evals.verify_reported`, which re-derives every
  reported number from the committed bundles named in `evals/reported.json`
  and fails if any cell drifts.
- Evidence artifacts live in the committed `evals/results/<stamp>-<arm>/`
  bundles — CHANGELOG entries link to those. `evals/out/` is ad-hoc only.
- Baselines (numbers we compare against) are pinned in `evals/reported.json`
  and re-derived by `make verify` — a green pytest is not evidence the eval
  passed.
