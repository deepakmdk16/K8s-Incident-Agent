# Rules-only diagnosis — t3-quiet-selector-loud-crashloop

Deterministic analyzer output. No model was called.

## Root cause

`service/search/search` — The Service selector does not match the pod labels, so its Endpoints object has no addresses.

Selected by analyzer `endpoints`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `endpoints`: service search selects {'app': 'search-api'} and its Endpoints object has no subsets (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n search -o wide`
2. `kubectl get events -n search --sort-by=.lastTimestamp`
3. `kubectl describe service search -n search`

```json
{
  "case_id": "t3-quiet-selector-loud-crashloop",
  "failing_resource": {
    "kind": "service",
    "namespace": "search",
    "name": "search"
  },
  "mechanism": "The Service selector does not match the pod labels, so its Endpoints object has no addresses.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
