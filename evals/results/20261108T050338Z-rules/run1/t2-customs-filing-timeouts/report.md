# Rules-only diagnosis — t2-customs-filing-timeouts

Deterministic analyzer output. No model was called.

## Root cause

`service/customs-docs/manifest-renderer` — The Service selector does not match the pod labels, so its Endpoints object has no addresses.

Selected by analyzer `endpoints`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `endpoints`: service manifest-renderer selects {'app': 'manifest-render'} and its Endpoints object has no subsets (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n customs-docs -o wide`
2. `kubectl get events -n customs-docs --sort-by=.lastTimestamp`
3. `kubectl describe service manifest-renderer -n customs-docs`

```json
{
  "case_id": "t2-customs-filing-timeouts",
  "failing_resource": {
    "kind": "service",
    "namespace": "customs-docs",
    "name": "manifest-renderer"
  },
  "mechanism": "The Service selector does not match the pod labels, so its Endpoints object has no addresses.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
