# Rules-only diagnosis — t1-imagepull-bogus-tag

Deterministic analyzer output. No model was called.

## Root cause

`deployment/web/storefront` — The image tag referenced by the container could not be pulled from the registry.

Selected by analyzer `image-pull`, which is the highest-precedence of 2 analyzer(s) that fired.

Verdict: probable.

## Evidence chain

- `image-pull`: pod storefront-68b686c56f-c7tvt container storefront is waiting with reason ImagePullBackOff (object state read from the snapshot)
- `image-pull`: pod storefront-68b686c56f-d4pp7 container storefront is waiting with reason ErrImagePull (object state read from the snapshot)

## Investigation ledger

- `image-pull` on `deployment/storefront` also matched and was NOT ruled out — it was dropped by precedence order alone, on no evidence.

## Verification recipe

1. `kubectl get pods -n web -o wide`
2. `kubectl get events -n web --sort-by=.lastTimestamp`
3. `kubectl describe deployment storefront -n web`

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {
    "kind": "deployment",
    "namespace": "web",
    "name": "storefront"
  },
  "mechanism": "The image tag referenced by the container could not be pulled from the registry.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
