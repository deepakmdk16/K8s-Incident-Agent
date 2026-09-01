# Rules-only diagnosis — t1-unschedulable-cpu-requests

Deterministic analyzer output. No model was called.

## Root cause

`deployment/fraud/fraud-scoring` — The pod cannot be scheduled because no node has sufficient allocatable CPU for its requests.

Selected by analyzer `unschedulable`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `unschedulable`: pod fraud-scoring-596445859d-thcdh is Pending with condition reason Unschedulable: 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling. (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n fraud -o wide`
2. `kubectl get events -n fraud --sort-by=.lastTimestamp`
3. `kubectl describe deployment fraud-scoring -n fraud`

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "fraud",
    "name": "fraud-scoring"
  },
  "mechanism": "The pod cannot be scheduled because no node has sufficient allocatable CPU for its requests.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
