# Rules-only diagnosis — t2-route-planning-degraded

Deterministic analyzer output. No model was called.

## Root cause

`deployment/dispatch/route-optimizer` — The application container exits with a fatal error immediately at startup, so the pod enters a restart back-off.

Selected by analyzer `crashloop`, which is the highest-precedence of 2 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `crashloop`: pod route-optimizer-78fc6747d7-22sfp container optimizer is waiting with reason CrashLoopBackOff (object state read from the snapshot)
- `crashloop`: pod route-optimizer-78fc6747d7-mqwkc container optimizer is waiting with reason CrashLoopBackOff (object state read from the snapshot)

## Investigation ledger

- `crashloop` on `deployment/route-optimizer` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n dispatch -o wide`
2. `kubectl get events -n dispatch --sort-by=.lastTimestamp`
3. `kubectl describe deployment route-optimizer -n dispatch`

```json
{
  "case_id": "t2-route-planning-degraded",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "dispatch",
    "name": "route-optimizer"
  },
  "mechanism": "The application container exits with a fatal error immediately at startup, so the pod enters a restart back-off.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
