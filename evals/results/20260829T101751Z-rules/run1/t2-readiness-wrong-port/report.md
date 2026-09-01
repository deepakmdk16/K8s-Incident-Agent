# Rules-only diagnosis — t2-readiness-wrong-port

Deterministic analyzer output. No model was called.

## Root cause

`deployment/orders/orders-api` — The readiness probe fails, so the pod never becomes ready.

Selected by analyzer `readiness`, which is the highest-precedence of 2 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `readiness`: pod orders-api-7cc5bcf4c7-lst42 has Readiness probe failed events (object state read from the snapshot)
- `readiness`: pod orders-api-7cc5bcf4c7-pcspl has Readiness probe failed events (object state read from the snapshot)

## Investigation ledger

- `readiness` on `deployment/orders-api` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n orders -o wide`
2. `kubectl get events -n orders --sort-by=.lastTimestamp`
3. `kubectl describe deployment orders-api -n orders`

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "orders",
    "name": "orders-api"
  },
  "mechanism": "The readiness probe fails, so the pod never becomes ready.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
