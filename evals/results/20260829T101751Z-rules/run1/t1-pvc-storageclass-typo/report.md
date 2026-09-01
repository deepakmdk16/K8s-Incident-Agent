# Rules-only diagnosis — t1-pvc-storageclass-typo

Deterministic analyzer output. No model was called.

## Root cause

`statefulset/analytics/metrics-db` — The pod cannot be scheduled because no node has sufficient allocatable CPU for its requests.

Selected by analyzer `unschedulable`, which is the highest-precedence of 3 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `unschedulable`: pod metrics-db-0 is Pending with condition reason Unschedulable: 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found (object state read from the snapshot)
- `unbound-pvc`: persistentvolumeclaim data-metrics-db-0 is in phase Pending (object state read from the snapshot)
- `endpoints`: service metrics-db selects {'app': 'metrics-db'} and its Endpoints object has no subsets (object state read from the snapshot)

## Investigation ledger

- `unbound-pvc` on `statefulset/metrics-db` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.
- `endpoints` on `service/metrics-db` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n analytics -o wide`
2. `kubectl get events -n analytics --sort-by=.lastTimestamp`
3. `kubectl describe statefulset metrics-db -n analytics`

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {
    "kind": "statefulset",
    "namespace": "analytics",
    "name": "metrics-db"
  },
  "mechanism": "The pod cannot be scheduled because no node has sufficient allocatable CPU for its requests.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
