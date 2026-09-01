## Root cause

The inventory-sync worker runs as ServiceAccount inventory-sync in namespace inventory and reads its feed configuration from the Kubernetes API. The only RoleBinding in the namespace, inventory-reader-binding, grants Role/inventory-reader (get,list on configmaps) to a subject named "inventory-synk" — a misspelling of the ServiceAccount name "inventory-sync" that actually exists. Because no RoleBinding names the identity the pod presents, its API read is rejected with HTTP 403 Forbidden, so the worker never retrieves fresh data and keeps serving its stale inventory snapshot, which is what the data-freshness monitor paged on. The pod stays Running and Ready, which is why the workload looks healthy while the feed is frozen.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The paged workload is Running and Ready with no restarts, so the failure is not a crash.
   source: namespace_overview(inventory) — verified
   > pod/inventory-sync-5cf949f7f9-czxsq phase=Running labels={app=inventory-sync, pod-template-hash=5cf949f7f9} node=incident-lab-control-plane sync(ready=True,restarts=0)
2. [symptom] The worker's fetch is rejected with 403 and it falls back to stale data, matching the InventoryCountsStale page.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
3. [symptom] The worker explicitly reports serving stale data after the 403.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > serving stale inventory snapshot
4. [link] The deployment runs as ServiceAccount inventory-sync.
   source: get_object({"kind": "deployments", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync",
5. [defect] RoleBinding inventory-reader-binding binds Role/inventory-reader to a ServiceAccount subject named inventory-synk, which is not the ServiceAccount the pod runs as.
   source: describe({"kind": "rolebinding", "name": "inventory-reader-binding", "namespace": "inventory"}) — verified
   > Name:         inventory-reader-binding
6. [defect] The subject of inventory-reader-binding is the misspelled ServiceAccount inventory-synk.
   source: describe({"kind": "rolebinding", "name": "inventory-reader-binding", "namespace": "inventory"}) — verified
   > ServiceAccount  inventory-synk  inventory
7. [defect] The snapshot of the RoleBinding object itself shows the wrong subject name.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk",
8. [link] The ServiceAccount inventory-sync exists, and the only RoleBinding names a subject that does not match it.
   source: find_consumers({"kind": "serviceaccounts", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
9. [link] The Role that would grant the needed access exists and grants get/list on configmaps; only the binding subject is wrong.
   source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
   > "resources": [
   >         "configmaps"
   >       ],
   >       "verbs": [
   >         "get",
   >         "list"
   >       ]

## Investigation ledger

- The ServiceAccount named by the pod template does not exist (identity missing rather than unbound). — ruled out: Both the default and inventory-sync ServiceAccounts exist in the namespace, so the identity resolves; only the binding subject is misspelled.
  source: find_consumers({"kind": "serviceaccounts", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync
- The Role itself is missing or grants the wrong verbs/resources. — ruled out: Role inventory-reader exists and already grants get and list on configmaps, so the permission set is adequate.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "name": "inventory-reader",
- The pod is crashing or its script ConfigMap mount is broken. — ruled out: ConfigMap inventory-sync-scripts exists with the run.sh key the volume mount needs, and the container started and ran the script.
  source: get_object({"kind": "configmaps", "name": "inventory-sync-scripts", "namespace": "inventory"}) — verified
  > "dataKeys": [
  >     "run.sh"
  >   ],

## Verification recipe

1. `kubectl -n inventory describe rolebinding inventory-reader-binding` — expect to see: inventory-synk  [PRESENT]
2. `kubectl -n inventory get sa` — expect to see: serviceaccounts that exist in inventory: default, inventory-sync  [PRESENT]
3. `kubectl -n inventory logs deploy/inventory-sync | tail -5` — expect to see: 403 Forbidden  [PRESENT]
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
  "mechanism": "RoleBinding inventory-reader-binding in namespace inventory has .subjects[0].name set to \"inventory-synk\" instead of the existing ServiceAccount \"inventory-sync\" that deployment/inventory-sync runs as, so Role/inventory-reader (get,list on configmaps) is bound to no real identity and the worker's API fetch is rejected with \"HTTP/1.1 403 Forbidden\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
