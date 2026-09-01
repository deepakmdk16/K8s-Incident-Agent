# Rules-only diagnosis — t2-rbac-sync-forbidden

Deterministic analyzer output. No model was called.

## Root cause

No analyzer signature matched any object in namespace `inventory`.
The rules engine cannot diagnose this incident.

## Evidence chain

No signature matched, so there is no evidence to cite.

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n inventory -o wide`
2. `kubectl get events -n inventory --sort-by=.lastTimestamp`

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {
    "kind": "unknown",
    "namespace": "inventory",
    "name": "unknown"
  },
  "mechanism": "No analyzer signature matched any object in the namespace.",
  "verdict": "inconclusive",
  "missing_evidence": "a signature for this failure mode, which the analyzer list does not contain"
}
```
