# Rules-only diagnosis — t2-checkout-release-stalled

Deterministic analyzer output. No model was called.

## Root cause

`deployment/checkout/checkout-api` — The Deployment rollout is not progressing; the new ReplicaSet cannot reach its desired replica count.

Selected by analyzer `rollout`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `rollout`: deployment checkout-api has Progressing=False, reason ProgressDeadlineExceeded (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n checkout -o wide`
2. `kubectl get events -n checkout --sort-by=.lastTimestamp`
3. `kubectl describe deployment checkout-api -n checkout`

```json
{
  "case_id": "t2-checkout-release-stalled",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "checkout",
    "name": "checkout-api"
  },
  "mechanism": "The Deployment rollout is not progressing; the new ReplicaSet cannot reach its desired replica count.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
