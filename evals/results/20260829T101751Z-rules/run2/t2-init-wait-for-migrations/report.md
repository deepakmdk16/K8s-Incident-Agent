# Rules-only diagnosis — t2-init-wait-for-migrations

Deterministic analyzer output. No model was called.

## Root cause

`deployment/billing/billing-api` — The init container does not complete, so the pod is blocked before the application container starts.

Selected by analyzer `init-container`, which is the highest-precedence of 2 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `init-container`: pod billing-api-ccb44c44c-89dn7 init container wait-for-db is not ready (object state read from the snapshot)
- `init-container`: pod billing-api-ccb44c44c-m756m init container wait-for-db is not ready (object state read from the snapshot)

## Investigation ledger

- `init-container` on `deployment/billing-api` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n billing -o wide`
2. `kubectl get events -n billing --sort-by=.lastTimestamp`
3. `kubectl describe deployment billing-api -n billing`

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "billing",
    "name": "billing-api"
  },
  "mechanism": "The init container does not complete, so the pod is blocked before the application container starts.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
