## Root cause

The inventory-sync worker runs as ServiceAccount inventory/inventory-sync and reads its feed configuration from the Kubernetes API. The only RoleBinding in the namespace, RoleBinding inventory/inventory-reader-binding, grants Role inventory/inventory-reader (get and list on configmaps) to a subject named "inventory-synk", which is not a ServiceAccount that exists in the namespace — the accounts that exist are default and inventory-sync. Because of that one-character subject name typo the worker's identity holds no permissions, its API fetch is rejected with HTTP/1.1 403 Forbidden, and the pod falls back to serving a stale inventory snapshot while staying Running and Ready, so the storefront counts never refresh.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The paged worker pod is Running and Ready with no restarts, so the staleness is not a crash.
   source: namespace_overview(inventory) — verified
   > pod/inventory-sync-5cf949f7f9-czxsq phase=Running labels={app=inventory-sync, pod-template-hash=5cf949f7f9} node=incident-lab-control-plane sync(ready=True,restarts=0)
2. [symptom] The worker's own logs show its fetch is rejected with 403.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
3. [symptom] After the 403 the worker explicitly serves stale data, matching the data-freshness alert.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > serving stale inventory snapshot
4. [link] The Deployment runs the pod as ServiceAccount inventory-sync.
   source: get_object({"kind": "deployment", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
5. [defect] The only RoleBinding names a subject that does not match the running ServiceAccount.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
6. [defect] The RoleBinding spec itself carries the misspelled ServiceAccount subject.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk"
7. [link] The Role that would have granted the needed read access covers configmaps with get and list.
   source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
   > "resources": [
   >         "configmaps"
   >       ],

## Investigation ledger

- The ServiceAccount named by the Deployment does not exist, so the pod has no identity at all. — ruled out: The ServiceAccount inventory-sync does exist in the namespace; only the binding's subject name is wrong.
  source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync
- The Role grants the wrong resources or verbs, so even a correct binding would not help. — ruled out: Role inventory-reader already grants get and list on configmaps, which is what the worker needs to read its feed configuration.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "verbs": [
  >         "get",
  >         "list"
  >       ]
- A missing or misnamed ConfigMap reference in the pod template breaks the sync. — ruled out: Both ConfigMaps the workload depends on exist with the expected keys, so the failure is not an unresolved config reference.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "dataKeys": [
  >       "refresh_seconds",
  >       "warehouse_feed_url"
  >     ],

## Verification recipe

1. `kubectl -n inventory get rolebinding inventory-reader-binding -o yaml` — expect to see: inventory-synk  [PRESENT]
2. `kubectl -n inventory logs deploy/inventory-sync` — expect to see: 403 Forbidden  [PRESENT]
3. `kubectl -n inventory get serviceaccounts` — expect to see: serviceaccounts that exist in inventory: default, inventory-sync  [PRESENT]
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
  "mechanism": "RoleBinding inventory/inventory-reader-binding has .subjects[0].name set to \"inventory-synk\" instead of \"inventory-sync\", the ServiceAccount that actually exists and that the paged worker runs as, so Role inventory/inventory-reader (get and list on configmaps) is bound to no existing identity. The worker's read of its feed configuration is therefore rejected \u2014 \"sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden\" \u2014 and it falls back to \"serving stale inventory snapshot\" instead of fetching fresh counts.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
