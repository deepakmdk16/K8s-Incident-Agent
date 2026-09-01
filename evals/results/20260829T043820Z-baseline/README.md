# DISCLOSED-PARTIAL RUN — not the headline number

Second poisoned bundle, different mechanism than 20260829T022557Z: a
**configured monthly API usage limit** tripped mid-run ("You have reached
your specified API usage limits. You will regain access on 2026-09-01") —
credits were present; the cap was the binding constraint. 14 of 36
case-runs failed on that 400 and are recorded as failed rows; the summary
must not be quoted.

Clean subset (rows.jsonl `error` field): run 1 complete — 8/12 (T1 5/5,
T2 1/5, T3 2/2, confirmed-wrong 3); run 2 partial 10 cases — 9 correct.
Cost of the 22 clean case-runs: $2.4681.

Disposition: harness now ABORTS on billing/limit errors instead of writing
doomed rows (evals/run_eval.py InfrastructureError, added in the same
commit); clean re-run follows the operator raising the cap. See
docs/failure-modes.md 2026-08-29 (escalation entry).
