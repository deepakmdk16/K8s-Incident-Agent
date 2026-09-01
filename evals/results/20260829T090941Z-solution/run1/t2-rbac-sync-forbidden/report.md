## Root cause

The inventory-sync worker authenticates to the Kubernetes API as ServiceAccount inventory/inventory-sync (its pod log prints "serviceaccount=inventory-sync"), but the only RoleBinding in the namespace, RoleBinding inventory/inventory-reader-binding, names the subject "inventory-synk" — a ServiceAccount that does not exist in the namespace (only default and inventory-sync exist). Because no binding grants Role inventory/inventory-reader to the identity the pod actually runs as, every read the worker makes is rejected with "HTTP/1.1 403 Forbidden", and it falls back to "serving stale inventory snapshot". The pod stays Running and Ready, so the deployment looks healthy while the storefront feed is frozen — matching the data-freshness page.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The paged workload is running but its data fetch is rejected and it serves stale data
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 50}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
2. [symptom] The worker explicitly falls back to a stale snapshot, matching the frozen-feed page
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 50}) — verified
   > serving stale inventory snapshot
3. [link] The pod runs as ServiceAccount inventory-sync
   source: get_object({"kind": "deployment", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
4. [defect] The only RoleBinding names a subject that is not the pod's service account
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk"
5. [defect] No RoleBinding subject matches the service account actually in use; the misspelled account does not exist
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
6. [link] The Role that would have granted the needed read access exists and covers configmaps
   source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
   > "verbs": [
   >         "get",
   >         "list"
   >       ]
7. [link] Only default and inventory-sync service accounts exist in the namespace
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > serviceaccounts that exist in inventory: default, inventory-sync

## Investigation ledger

- The Deployment references a nonexistent ServiceAccount — ruled out: The ServiceAccount named by the Deployment does exist in the namespace, so the pod's identity resolves correctly
  source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync
- The Role itself is missing or lacks the verbs the worker needs — ruled out: Role inventory/inventory-reader exists and grants get and list on configmaps; it is simply bound to the wrong subject
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "resources": [
  >         "configmaps"
  >       ]
- A missing or mis-keyed ConfigMap or volume mount breaks the worker — ruled out: Both ConfigMaps referenced exist with their expected keys, and the pod started and mounted its script successfully
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "dataKeys": [
  >       "run.sh"
  >     ]
- Pod crash, restart loop, or scheduling failure — ruled out: The pod is Running and Ready with zero restarts, so the failure is authorization, not lifecycle
  source: namespace_overview(inventory) — verified
  > sync(ready=True,restarts=0)

## Verification recipe

1. `kubectl get rolebinding inventory-reader-binding -n inventory -o jsonpath='{.subjects[0].name}'` — expect to see: inventory-synk  [PRESENT]
2. `kubectl get deploy inventory-sync -n inventory -o jsonpath='{.spec.template.spec.serviceAccountName}'` — expect to see: "serviceAccountName": "inventory-sync"  [PRESENT]
3. `kubectl logs -n inventory inventory-sync-5cf949f7f9-czxsq --tail=50` — expect to see: 403 Forbidden  [PRESENT]
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
  "mechanism": "RoleBinding inventory/inventory-reader-binding has .subjects[0].name set to \"inventory-synk\", but the ServiceAccount the pod runs as is inventory/inventory-sync (.spec.serviceAccountName: \"inventory-sync\"), so the get and list verbs on configmaps granted by Role inventory/inventory-reader bind to a nonexistent identity. The worker's API reads are therefore denied with \"wget: server returned error: HTTP/1.1 403 Forbidden\" and it falls back to \"serving stale inventory snapshot\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
