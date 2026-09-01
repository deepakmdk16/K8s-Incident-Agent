## Root cause

The inventory-sync worker in namespace inventory runs as ServiceAccount inventory/inventory-sync and reads its feed configuration from the Kubernetes API, but RoleBinding inventory/inventory-reader-binding grants Role inventory/inventory-reader to a subject named "inventory-synk" — a name that does not exist in the namespace (only "default" and "inventory-sync" exist). Because of the one-character typo, the identity the pod actually uses is bound to nothing, so its API read is rejected with "HTTP/1.1 403 Forbidden" and the worker falls back to "serving stale inventory snapshot". The pod itself stays Running and Ready with zero restarts, which is why the storefront feed silently froze rather than alerting as a crash. Fixing the subject name in the RoleBinding to inventory-sync restores the get/list permission on configmaps that the sync needs.

Remediation: edit RoleBinding inventory/inventory-reader-binding, field `subjects[0].name`: `inventory-synk` -> `inventory-sync`.

## Evidence chain

1. [symptom] The paged worker pod is Running and Ready with no restarts, so nothing looks unhealthy.
   source: namespace_overview(inventory) — verified
   > pod/inventory-sync-5cf949f7f9-czxsq phase=Running labels={app=inventory-sync, pod-template-hash=5cf949f7f9} node=incident-lab-control-plane sync(ready=True,restarts=0)
2. [symptom] The worker's API fetch is denied and it falls back to stale data, which is exactly the frozen feed described in the page.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden
3. [symptom] The worker explicitly reports serving stale data after the denial.
   source: get_logs({"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "tail": 60}) — verified
   > serving stale inventory snapshot
4. [link] The Deployment runs the pod under ServiceAccount inventory-sync.
   source: get_object({"kind": "deployment", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > "serviceAccountName": "inventory-sync"
5. [defect] The only RoleBinding in the namespace names a subject 'inventory-synk', not the ServiceAccount the pod uses.
   source: get_object({"kind": "rolebindings", "namespace": "inventory"}) — verified
   > "name": "inventory-synk",
6. [defect] The binding subject does not match the workload's service account, and no such service account exists in the namespace.
   source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
   > rolebinding/inventory-reader-binding subjects[].name='inventory-synk' roleRef=Role/inventory-reader (does not match)
7. [link] The Role that would grant the needed API access exists and carries the right rules; only the binding subject is wrong.
   source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
   > "verbs": [
   >         "get",
   >         "list"
   >       ]

## Investigation ledger

- The Role itself is missing the permissions the sync needs (wrong resources/verbs). — ruled out: Role inventory/inventory-reader already grants get and list on configmaps, so the rule set is not the defect.
  source: get_object({"kind": "roles", "name": "inventory-reader", "namespace": "inventory"}) — verified
  > "resources": [
  >         "configmaps"
  >       ],
- The feed configuration object the worker reads is missing or misnamed. — ruled out: ConfigMap inventory/inventory-sources exists with the feed URL and refresh interval keys, so the read target is present.
  source: get_object({"kind": "configmaps", "namespace": "inventory"}) — verified
  > "dataKeys": [
  >       "refresh_seconds",
  >       "warehouse_feed_url"
  >     ],
- The pod or container is crashing/unschedulable and that is why counts stopped updating. — ruled out: The pod is Running and Ready with zero restarts, so it is a permission failure inside a healthy pod, not a pod lifecycle failure.
  source: namespace_overview(inventory) — verified
  > sync(ready=True,restarts=0)
- The Deployment points at the wrong or a nonexistent service account. — ruled out: ServiceAccount inventory/inventory-sync does exist in the namespace and is the one the Deployment names, so the workload side of the reference resolves.
  source: find_consumers({"kind": "serviceaccount", "name": "inventory-sync", "namespace": "inventory"}) — verified
  > serviceaccounts that exist in inventory: default, inventory-sync

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
  "mechanism": "RoleBinding inventory/inventory-reader-binding has .subjects[0].name set to \"inventory-synk\" instead of the existing ServiceAccount name \"inventory-sync\", so Role inventory/inventory-reader (get, list on configmaps) is bound to a nonexistent subject and Deployment inventory/inventory-sync's identity holds no permissions. The worker's API fetch is therefore rejected \u2014 \"sync fetch error: wget: server returned error: HTTP/1.1 403 Forbidden\" \u2014 and it logs \"serving stale inventory snapshot\" while its container stays ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
