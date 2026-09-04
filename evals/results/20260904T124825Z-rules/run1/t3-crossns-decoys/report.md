# Rules-only diagnosis — t3-crossns-decoys

Deterministic analyzer output. No model was called.

## Root cause

`deployment/storefront/checkout-api` — The readiness probe fails, so the pod never becomes ready.

Selected by analyzer `readiness`, which is the highest-precedence of 2 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `readiness`: pod checkout-api-7db48f7c7b-6fb2v has Readiness probe failed events (object state read from the snapshot)
- `readiness`: pod checkout-api-7db48f7c7b-g7299 has Readiness probe failed events (object state read from the snapshot)

## Investigation ledger

- `readiness` on `deployment/checkout-api` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n storefront -o wide`
2. `kubectl get events -n storefront --sort-by=.lastTimestamp`
3. `kubectl describe deployment checkout-api -n storefront`

```json
{
  "case_id": "t3-crossns-decoys",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "storefront",
    "name": "checkout-api"
  },
  "mechanism": "The readiness probe fails, so the pod never becomes ready.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
