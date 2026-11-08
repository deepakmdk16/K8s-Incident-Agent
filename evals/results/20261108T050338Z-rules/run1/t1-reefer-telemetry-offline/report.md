# Rules-only diagnosis — t1-reefer-telemetry-offline

Deterministic analyzer output. No model was called.

## Root cause

`deployment/cold-chain/reefer-monitor` — The pod cannot be scheduled because no node has sufficient allocatable CPU for its requests.

Selected by analyzer `unschedulable`, which is the highest-precedence of 2 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `unschedulable`: pod reefer-monitor-849469b48c-b6mnr is Pending with condition reason Unschedulable: 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling. (object state read from the snapshot)
- `unschedulable`: pod reefer-monitor-849469b48c-jjvkg is Pending with condition reason Unschedulable: 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling. (object state read from the snapshot)

## Investigation ledger

- `unschedulable` on `deployment/reefer-monitor` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n cold-chain -o wide`
2. `kubectl get events -n cold-chain --sort-by=.lastTimestamp`
3. `kubectl describe deployment reefer-monitor -n cold-chain`

```json
{
  "case_id": "t1-reefer-telemetry-offline",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "cold-chain",
    "name": "reefer-monitor"
  },
  "mechanism": "The pod cannot be scheduled because no node has sufficient allocatable CPU for its requests.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
