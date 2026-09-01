# Rules-only diagnosis — t1-oom-cache-warmup

Deterministic analyzer output. No model was called.

## Root cause

`deployment/recs/recommendations` — The container was killed after exceeding its memory limit.

Selected by analyzer `oom`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `oom`: pod recommendations-85fd7764f4-p9rw8 container server last terminated with reason OOMKilled after 2 restarts (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n recs -o wide`
2. `kubectl get events -n recs --sort-by=.lastTimestamp`
3. `kubectl describe deployment recommendations -n recs`

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "recs",
    "name": "recommendations"
  },
  "mechanism": "The container was killed after exceeding its memory limit.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
