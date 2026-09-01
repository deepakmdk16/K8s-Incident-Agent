# Rules-only diagnosis — t2-selector-drift-empty-endpoints

Deterministic analyzer output. No model was called.

## Root cause

`service/shop/catalog` — The Service selector does not match the pod labels, so its Endpoints object has no addresses.

Selected by analyzer `endpoints`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `endpoints`: service catalog selects {'app': 'catalog-api'} and its Endpoints object has no subsets (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n shop -o wide`
2. `kubectl get events -n shop --sort-by=.lastTimestamp`
3. `kubectl describe service catalog -n shop`

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {
    "kind": "service",
    "namespace": "shop",
    "name": "catalog"
  },
  "mechanism": "The Service selector does not match the pod labels, so its Endpoints object has no addresses.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
