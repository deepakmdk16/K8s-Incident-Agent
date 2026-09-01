# Rules-only diagnosis — t1-crashloop-missing-env

Deterministic analyzer output. No model was called.

## Root cause

`deployment/payments/checkout-worker` — The application container exits with a fatal error immediately at startup, so the pod enters a restart back-off.

Selected by analyzer `crashloop`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `crashloop`: pod checkout-worker-66bfcdfc47-d9gdj container worker is waiting with reason CrashLoopBackOff (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n payments -o wide`
2. `kubectl get events -n payments --sort-by=.lastTimestamp`
3. `kubectl describe deployment checkout-worker -n payments`

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "payments",
    "name": "checkout-worker"
  },
  "mechanism": "The application container exits with a fatal error immediately at startup, so the pod enters a restart back-off.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
