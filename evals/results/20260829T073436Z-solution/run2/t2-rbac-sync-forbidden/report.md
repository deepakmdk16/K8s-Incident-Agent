## Root cause

The RoleBinding inventory-reader-binding in namespace inventory grants Role/inventory-reader (get/list on configmaps) to a ServiceAccount subject spelled "inventory-synk", which is not a ServiceAccount that exists in the namespace — the only accounts present are "default" and "inventory-sync". The Deployment inventory-sync runs its pod as ServiceAccountName "inventory-sync", so that identity holds no permission to read ConfigMaps and its API fetch is rejected with HTTP 403 Forbidden. The pod stays Running and Ready but logs "sync fetch error: ... 403 Forbidden" and then "serving stale inventory snapshot", which is exactly the frozen storefront inventory feed described in the page. Fixing the single typo in the RoleBinding subject name restores the grant to the identity the workload actually uses.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The page reports the inventory feed frozen while the worker is running.
   source: the page — verified
   > Storefront inventory counts have not updated for over 30 minutes
2. [symptom] The pod is Running and Ready with no restarts, so the failure is not a crash.
   source: namespace_overview(inventory) — verified
   > pod/inventory-sync-5cf949f7f9-czxsq phase=Running labels={app=inventory-sync, pod-template-hash=5cf949f7f9} node=incident-lab-control-plane sync(ready=True,restarts=0)
3. [symptom] The worker's fetch is rejected with 403 and it falls back to stale data.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
4. [link] The worker runs as ServiceAccount inventory-sync.
   source: get_object({"kind": "deployment", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
5. [defect] The only RoleBinding in the namespace names a subject that does not match the workload's ServiceAccount.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
6. [defect] The RoleBinding subject is literally spelled inventory-synk.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk",
7. [link] The Role it references does grant the needed configmap reads, so the rules themselves are adequate.
   source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
   > "resources": [
   >         "configmaps"
   >       ],

## Investigation ledger

- The ServiceAccount inventory-sync is missing, so the pod has no identity. — ruled out: Both default and inventory-sync ServiceAccounts exist in the namespace; the account named by the Deployment is present.
  source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync
- The Role inventory-reader lacks the verbs/resources the worker needs, so even a correct binding would 403. — ruled out: The Role already grants get and list on configmaps, which is what the worker reads.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "verbs": [
  >         "get",
  >         "list"
  >       ]
- The source ConfigMap the worker reads is missing or misnamed. — ruled out: Both inventory-sources (with warehouse_feed_url) and the script ConfigMap exist in the namespace.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "name": "inventory-sources",
- The container is crashing or the image/mount reference is broken. — ruled out: The sync container is ready with zero restarts, so no crash loop or unresolved mount is involved.
  source: namespace_overview(inventory) — verified
  > sync(ready=True,restarts=0)

## Verification recipe

1. `kubectl -n inventory get rolebinding inventory-reader-binding -o jsonpath='{.subjects[0].name}'` — expect to see: inventory-synk  [PRESENT]
2. `kubectl -n inventory logs deploy/inventory-sync | tail -20` — expect to see: 403 Forbidden  [PRESENT]
3. `kubectl -n inventory get deploy inventory-sync -o jsonpath='{.spec.template.spec.serviceAccountName}'` — expect to see: "serviceAccountName": "inventory-sync"  [PRESENT]
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
  "mechanism": "The RoleBinding inventory-reader-binding in namespace inventory has .subjects[0].name set to \"inventory-synk\" instead of \"inventory-sync\", so it binds Role/inventory-reader to a ServiceAccount name that does not exist in the namespace and grants get/list on configmaps to nobody real. The identity the workload actually presents therefore carries no configmap read permission and its API fetch is rejected by the authorizer with HTTP 403 Forbidden.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
