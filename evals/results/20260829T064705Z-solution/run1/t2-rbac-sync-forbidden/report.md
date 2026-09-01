## Root cause

The inventory-sync worker runs as ServiceAccount inventory-sync in namespace inventory, and it reads its feed configuration from the Kubernetes API. The only RoleBinding in that namespace, inventory-reader-binding, grants Role/inventory-reader (get/list on configmaps) to a subject named "inventory-synk" — a name that does not exist; the actual ServiceAccount is "inventory-sync". Because no binding names the pod's identity, its API reads are rejected with HTTP 403 Forbidden, so the worker logs "sync fetch error" and keeps serving the stale inventory snapshot while the pod itself stays Running and Ready, which is why counts have been frozen without any pod-level alarm.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The paged worker is Running and Ready yet reports a forbidden fetch and serves stale data.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 50}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
2. [symptom] The worker falls back to a stale snapshot, matching the InventoryCountsStale page.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 50}) — verified
   > serving stale inventory snapshot
3. [link] The Deployment runs the pod as ServiceAccount inventory-sync.
   source: get_object({"kind": "deployment", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
4. [defect] The only RoleBinding names a subject 'inventory-synk', not the ServiceAccount in use.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk"
5. [defect] The binding's subject does not match any existing ServiceAccount in the namespace.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
6. [link] Only 'default' and 'inventory-sync' ServiceAccounts exist; 'inventory-synk' does not.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > serviceaccounts that exist in inventory: default, inventory-sync
7. [link] The pod is confirmed to be running under the inventory-sync identity.
   source: describe({"kind": "pod", "name": "inventory-sync-5cf949f7f9-czxsq", "namespace": "inventory"}) — verified
   > Service Account:  inventory-sync

## Investigation ledger

- The Role itself lacks the verbs/resources the worker needs (permissions too narrow rather than unbound). — ruled out: Role/inventory-reader does grant get and list on configmaps, which is what the worker needs; the problem is that it reaches no existing subject.
  source: get_object({"kind": "roles", "namespace": "inventory"}) — verified
  > "resources": [
  >           "configmaps"
  >         ],
  >         "verbs": [
  >           "get",
  >           "list"
  >         ]
- A missing or misnamed ConfigMap reference (feed URL / script) is what breaks the sync. — ruled out: Both ConfigMaps the workload depends on exist with the expected keys, and the script volume mounted fine.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "dataKeys": [
  >       "refresh_seconds",
  >       "warehouse_feed_url"
  >     ]
- The pod is crashing, unscheduled, or failing an image pull. — ruled out: The pod is Running and Ready with zero restarts and no warning events, so the failure is at the API-authorization layer, not the pod lifecycle.
  source: describe({"kind": "pod", "name": "inventory-sync-5cf949f7f9-czxsq", "namespace": "inventory"}) — verified
  > Ready:          True
  >     Restart Count:  0

## Verification recipe

1. `kubectl get rolebinding inventory-reader-binding -n inventory -o yaml` — expect to see: inventory-synk  [PRESENT]
2. `kubectl get deployment inventory-sync -n inventory -o jsonpath='{.spec.template.spec.serviceAccountName}'` — expect to see: "serviceAccountName": "inventory-sync"  [PRESENT]
3. `kubectl logs -n inventory deploy/inventory-sync --tail=20` — expect to see: 403 Forbidden  [PRESENT]
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
  "mechanism": "RoleBinding inventory-reader-binding in namespace inventory has .subjects[0].name = \"inventory-synk\", but the ServiceAccount the Deployment runs as (.spec.template.spec.serviceAccountName) is \"inventory-sync\"; that one-character mismatch means Role/inventory-reader is bound to no existing identity, so the worker's API request for its inventory source data is denied with \"HTTP/1.1 403 Forbidden\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
