# Rules-only diagnosis — t2-yard-gate-release-blocked

Deterministic analyzer output. No model was called.

## Root cause

`deployment/yard-ops/gate-checkin` — The Deployment rollout is not progressing; the new ReplicaSet cannot reach its desired replica count.

Selected by analyzer `rollout`, which is the highest-precedence of 1 analyzer(s) that fired.

Verdict: confirmed.

## Evidence chain

- `rollout`: deployment gate-checkin has Progressing=False, reason ProgressDeadlineExceeded (object state read from the snapshot)

## Investigation ledger

The engine evaluates a fixed analyzer list and reports what matched. Signatures that did not match were not considered as hypotheses and carry no ruling-out evidence.

## Verification recipe

1. `kubectl get pods -n yard-ops -o wide`
2. `kubectl get events -n yard-ops --sort-by=.lastTimestamp`
3. `kubectl describe deployment gate-checkin -n yard-ops`

```json
{
  "case_id": "t2-yard-gate-release-blocked",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "yard-ops",
    "name": "gate-checkin"
  },
  "mechanism": "The Deployment rollout is not progressing; the new ReplicaSet cannot reach its desired replica count.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
