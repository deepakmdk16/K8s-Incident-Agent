# Rules-only diagnosis — t3-overlapping-config-and-oom

Deterministic analyzer output. No model was called.

## Root cause

`deployment/orders/orders-api` — The container cannot start because the ConfigMap it references was not found.

Selected by analyzer `config-ref`, which is the highest-precedence of 3 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `config-ref`: pod orders-api-6c64874687-8c47z container api is waiting with reason CreateContainerConfigError (object state read from the snapshot)
- `config-ref`: pod orders-api-6c64874687-t6d22 container api is waiting with reason CreateContainerConfigError (object state read from the snapshot)
- `oom`: pod orders-report-worker-5645b7fccf-zhjzb container worker last terminated with reason OOMKilled after 3 restarts (object state read from the snapshot)

## Investigation ledger

- `config-ref` on `deployment/orders-api` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.
- `oom` on `deployment/orders-report-worker` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n orders -o wide`
2. `kubectl get events -n orders --sort-by=.lastTimestamp`
3. `kubectl describe deployment orders-api -n orders`

```json
{
  "case_id": "t3-overlapping-config-and-oom",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "orders",
    "name": "orders-api"
  },
  "mechanism": "The container cannot start because the ConfigMap it references was not found.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
