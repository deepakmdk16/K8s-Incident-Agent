# Rules-only diagnosis — t2-quota-blocks-scale

Deterministic analyzer output. No model was called.

## Root cause

`resourcequota/checkout/checkout-quota` — The ResourceQuota in this namespace is exhausted, so new pods are rejected.

Selected by analyzer `quota`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `quota`: resourcequota checkout-quota is at its hard limit for pods (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n checkout -o wide`
2. `kubectl get events -n checkout --sort-by=.lastTimestamp`
3. `kubectl describe resourcequota checkout-quota -n checkout`

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {
    "kind": "resourcequota",
    "namespace": "checkout",
    "name": "checkout-quota"
  },
  "mechanism": "The ResourceQuota in this namespace is exhausted, so new pods are rejected.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
