## Root cause

The inventory-sync worker runs as ServiceAccount inventory-sync (set in the Deployment's .spec.template.spec.serviceAccountName), but the only RoleBinding in the namespace, inventory-reader-binding, grants Role/inventory-reader to a subject named "inventory-synk" — a ServiceAccount name that does not exist in the namespace (only "default" and "inventory-sync" exist). Because the binding names a non-existent identity, the running pod's identity holds no permission to read the inventory source ConfigMaps, so its API read is rejected with HTTP 403 Forbidden and the worker falls back to serving the stale inventory snapshot, which is what the data-freshness monitor paged on. The Role itself and the ConfigMaps it targets are correct; only the binding's subject name must be edited.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The paged workload is Running and Ready yet inventory data is stale.
   source: namespace_overview(inventory) — verified
   > pod/inventory-sync-5cf949f7f9-czxsq phase=Running labels={app=inventory-sync, pod-template-hash=5cf949f7f9} node=incident-lab-control-plane sync(ready=True,restarts=0)
2. [symptom] The worker's fetch is rejected with 403 and it serves stale data.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
3. [symptom] The worker explicitly falls back to a stale snapshot, matching the freshness alert.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > serving stale inventory snapshot
4. [link] The Deployment runs as ServiceAccount inventory-sync.
   source: get_object({"kind": "deployment", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
5. [defect] The only RoleBinding names a subject that does not match the workload's ServiceAccount, and the only accounts that exist are default and inventory-sync.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
6. [defect] The RoleBinding subject name is literally inventory-synk.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk",

## Investigation ledger

- The Role itself lacks the verbs/resources needed to read inventory ConfigMaps — ruled out: Role/inventory-reader already grants get and list on configmaps, so the grant content is correct; only its binding subject is wrong.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "resources": [
  >         "configmaps"
  >       ],
  >       "verbs": [
  >         "get",
  >         "list"
  >       ]
- The Deployment references a non-existent or misspelled ServiceAccount — ruled out: The ServiceAccount named by the Deployment exists in the namespace, so the pod identity resolves correctly.
  source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync
- The source ConfigMap or its keys are missing, so the worker has nothing to read — ruled out: Both the source ConfigMap with its feed keys and the script ConfigMap exist in the namespace.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "dataKeys": [
  >       "refresh_seconds",
  >       "warehouse_feed_url"
  >     ],
- The pod is crashing, unscheduled or restarting (a workload health problem rather than an RBAC problem) — ruled out: The pod is Running, Ready and has zero restarts, so the failure is in what it is permitted to do, not in whether it runs.
  source: namespace_overview(inventory) — verified
  > sync(ready=True,restarts=0)

## Verification recipe

1. `kubectl -n inventory get rolebinding inventory-reader-binding -o jsonpath='{.subjects[0].name}'` — expect to see: inventory-synk  [PRESENT]
2. `kubectl -n inventory get deployment inventory-sync -o jsonpath='{.spec.template.spec.serviceAccountName}'` — expect to see: "serviceAccountName": "inventory-sync"  [PRESENT]
3. `kubectl -n inventory logs inventory-sync-5cf949f7f9-czxsq --tail=20` — expect to see: 403 Forbidden  [PRESENT]
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
  "mechanism": "RoleBinding inventory-reader-binding in namespace inventory has .subjects[0].name set to \"inventory-synk\" instead of \"inventory-sync\", the ServiceAccount the Deployment actually runs as, so Role/inventory-reader is bound to no existing identity and the worker's read of the inventory source data is rejected by the API server with 403 Forbidden.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
