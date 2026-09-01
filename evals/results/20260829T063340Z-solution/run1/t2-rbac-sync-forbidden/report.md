## Root cause

The inventory-sync worker runs as ServiceAccount inventory-sync in namespace inventory, but the only RoleBinding in that namespace, inventory-reader-binding, lists its subject as ServiceAccount "inventory-synk" — a name that does not exist in the namespace (only "default" and "inventory-sync" do). Because the identity the pod actually uses is never bound to any role, its API reads are rejected with HTTP 403 Forbidden, so the worker cannot refresh the feed and keeps serving the stale inventory snapshot, which is what the data-freshness monitor paged on. Fixing the subject name to inventory-sync restores the grant.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The worker's fetch is refused with an authorization error.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
2. [symptom] The worker explicitly reports serving stale data, matching the page.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > serving stale inventory snapshot
3. [link] The Deployment runs as ServiceAccount inventory-sync.
   source: get_object({"kind": "deployments", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
4. [defect] The only RoleBinding names a subject that does not match the running ServiceAccount.
   source: find_consumers({"kind": "serviceaccounts", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
5. [defect] No ServiceAccount named inventory-synk exists in the namespace, so the binding grants nothing to anyone.
   source: find_consumers({"kind": "serviceaccounts", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > serviceaccounts that exist in inventory: default, inventory-sync
6. [defect] The RoleBinding spec literally carries the mistyped subject name.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk",

## Investigation ledger

- The worker pod is crashing, restarting or unscheduled — ruled out: The pod is Running and Ready with no restarts, so pod lifecycle is not the failure.
  source: namespace_overview(inventory) — verified
  > pod/inventory-sync-5cf949f7f9-czxsq phase=Running labels={app=inventory-sync, pod-template-hash=5cf949f7f9} node=incident-lab-control-plane sync(ready=True,restarts=0)
- A missing or mis-keyed source ConfigMap (inventory-sources) is starving the worker of its feed URL — ruled out: The ConfigMap exists with both expected keys, so the configuration data it needs is present.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "refresh_seconds",
- The Role lacks the verbs the worker needs — ruled out: Role inventory-reader already grants get and list on configmaps, so the permission set is adequate once it reaches the correct subject.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "configmaps"

## Verification recipe

1. `kubectl -n inventory get rolebinding inventory-reader-binding -o jsonpath='{.subjects[0].name}'` — expect to see: inventory-synk  [PRESENT]
2. `kubectl -n inventory get sa` — expect to see: serviceaccounts that exist in inventory: default, inventory-sync  [PRESENT]
3. `kubectl -n inventory logs deploy/inventory-sync` — expect to see: 403 Forbidden  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {
    "kind": "RoleBinding",
    "namespace": "inventory",
    "name": "inventory-reader-binding"
  },
  "mechanism": "RoleBinding inventory-reader-binding in namespace inventory has .subjects[0].name set to \"inventory-synk\" instead of \"inventory-sync\", the ServiceAccount the Deployment actually runs as via .spec.template.spec.serviceAccountName. That identity therefore holds no namespace grant, and the API server rejects the worker's sync fetch with \"HTTP/1.1 403 Forbidden\", after which the worker falls back to serving the previously cached snapshot.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
