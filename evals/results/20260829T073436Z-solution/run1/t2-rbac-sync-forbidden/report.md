## Root cause

The RoleBinding inventory-reader-binding in namespace inventory binds the Role inventory-reader (get/list on configmaps) to a ServiceAccount subject named "inventory-synk", but that ServiceAccount does not exist — the only ServiceAccounts in the namespace are "default" and "inventory-sync". The Deployment inventory-sync runs its pod as spec.serviceAccountName "inventory-sync", so that identity holds no permissions in the namespace and its API reads for the ConfigMap-backed feed configuration are rejected with HTTP 403 Forbidden. The container catches the error and keeps serving its last cached snapshot ("serving stale inventory snapshot"), so the pod stays Running and Ready with zero restarts while storefront inventory counts stop advancing — exactly the frozen feed the data-freshness monitor paged on. Fixing the one-character typo in the binding subject restores the read permission.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The paged workload is Running and Ready with no restarts, yet the feed is frozen.
   source: namespace_overview(inventory) — verified
   > pod/inventory-sync-5cf949f7f9-czxsq phase=Running labels={app=inventory-sync, pod-template-hash=5cf949f7f9} node=incident-lab-control-plane sync(ready=True,restarts=0)
2. [symptom] The worker's own logs show its API fetch rejected with 403 and it falling back to cached data.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
3. [symptom] The worker explicitly reports serving stale data after the 403.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > serving stale inventory snapshot
4. [link] The Deployment runs its pod under the ServiceAccount inventory-sync.
   source: get_object({"kind": "deployment", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
5. [defect] The only RoleBinding in the namespace names a subject that does not match the workload's ServiceAccount.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
6. [defect] The RoleBinding subject is literally spelled inventory-synk.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk",
7. [link] No ServiceAccount named inventory-synk exists in the namespace; the workload's account inventory-sync does.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > serviceaccounts that exist in inventory: default, inventory-sync

## Investigation ledger

- The Role itself lacks the permissions the worker needs (wrong verbs or resources). — ruled out: The Role inventory-reader already grants get and list on configmaps, which is what the worker reads; the grant simply never reaches the workload's identity.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "resources": [
  >         "configmaps"
  >       ],
  >       "verbs": [
  >         "get",
  >         "list"
  >       ]
- The Deployment points at a ServiceAccount that does not exist (typo on the workload side instead of the binding side). — ruled out: The ServiceAccount named by the Deployment exists in the namespace, so the workload side of the reference resolves.
  source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync
- The feed configuration ConfigMap or the script ConfigMap is missing or misnamed, so the worker has nothing to read. — ruled out: Both ConfigMaps exist with their expected keys, so the failure is authorization, not a missing object.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "refresh_seconds",
  >       "warehouse_feed_url"
  >     ],
  >     "kind": "ConfigMap",
  >     "metadata": {
  >       "creationTimestamp": "2026-08-29T02:11:44Z",
  >       "name": "inventory-sources",
- The pod is crashing, being restarted, or failing to schedule. — ruled out: The pod is Running and Ready with zero restarts, so the fault is behavioural rather than a pod lifecycle failure.
  source: namespace_overview(inventory) — verified
  > sync(ready=True,restarts=0)

## Verification recipe

1. `kubectl -n inventory get rolebinding inventory-reader-binding -o jsonpath='{.subjects[0].name}'` — expect to see: inventory-synk  [PRESENT]
2. `kubectl -n inventory get serviceaccounts` — expect to see: serviceaccounts that exist in inventory: default, inventory-sync  [PRESENT]
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
  "mechanism": "The RoleBinding inventory-reader-binding in namespace inventory names a nonexistent subject: .subjects[0].name is \"inventory-synk\" instead of the existing ServiceAccount \"inventory-sync\", so the Role inventory-reader (get/list on configmaps) is granted to nobody that runs. Every authorization check made under the identity inventory-sync therefore falls through to deny, and the binding continues to reference a subject that will never resolve.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
