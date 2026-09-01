## Root cause

The inventory-sync worker runs as ServiceAccount inventory-sync (Deployment .spec.template.spec.serviceAccountName), but the only RoleBinding in the namespace, inventory-reader-binding, grants Role/inventory-reader to a subject named "inventory-synk" — a misspelling of a ServiceAccount that does not exist in the namespace (only default and inventory-sync exist). With no binding naming its real identity, the worker's API read of the inventory source data is rejected with HTTP 403 Forbidden, so it logs the fetch error and keeps serving the stale inventory snapshot, which is what the data-freshness monitor paged on. The pod itself is healthy (Running, ready, zero restarts), which is why the symptom looks like a frozen feed rather than a crash.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The page reports inventory counts frozen while the worker pod is up.
   source: the page — verified
   > Storefront inventory counts have not updated for over 30 minutes
2. [symptom] The paged pod is Running and ready with no restarts, so this is not a crash.
   source: namespace_overview(inventory) — verified
   > phase=Running
3. [symptom] The worker's own logs show its fetch is rejected with 403 and it falls back to stale data.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 50}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
4. [symptom] The worker explicitly reports serving stale data after the 403.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 50}) — verified
   > serving stale inventory snapshot
5. [link] The Deployment runs the pod under ServiceAccount inventory-sync.
   source: get_object({"kind": "deployments", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
6. [defect] RoleBinding inventory-reader-binding names a subject that does not match the ServiceAccount the Deployment uses.
   source: find_consumers({"kind": "serviceaccounts", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
7. [defect] The RoleBinding subject name is misspelled in its own spec.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk",
8. [link] Only default and inventory-sync ServiceAccounts exist; inventory-synk does not.
   source: find_consumers({"kind": "serviceaccounts", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > serviceaccounts that exist in inventory: default, inventory-sync

## Investigation ledger

- The Role itself lacks the permissions the worker needs (wrong verbs/resources). — ruled out: Role inventory-reader already grants get and list on configmaps, which is what the worker reads; the grant never reaches the worker only because the binding subject is wrong.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "configmaps"
- The source ConfigMap or its keys are missing, so the worker has nothing to read. — ruled out: ConfigMap inventory-sources exists with its feed keys, and the script ConfigMap mounted by the Deployment exists too, so the failure is authorization, not a missing reference.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "warehouse_feed_url"
- The Deployment points at a nonexistent or wrong ServiceAccount. — ruled out: The ServiceAccount inventory-sync named by the Deployment does exist in the namespace, so the Deployment side of the reference resolves.
  source: find_consumers({"kind": "serviceaccounts", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync

## Verification recipe

1. `kubectl -n inventory get rolebinding inventory-reader-binding -o jsonpath='{.subjects[0].name}'` — expect to see: inventory-synk  [PRESENT]
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
  "mechanism": "The RoleBinding inventory-reader-binding in namespace inventory lists .subjects[0].name = \"inventory-synk\" instead of \"inventory-sync\", so it binds Role/inventory-reader to a ServiceAccount that does not exist and grants nothing to the identity the worker actually presents; the worker's read of the inventory source data is therefore rejected by the API server with \"HTTP/1.1 403 Forbidden\" and it falls back to serving a stale inventory snapshot.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
