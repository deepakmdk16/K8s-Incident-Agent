## Root cause

The Deployment billing/billing-api gates its application container behind an init container named wait-for-db, which polls the host given in its environment variable DB_HOST on port 5432 until it answers. That variable is set to "db-primary", but the only database Service in the billing namespace is named postgres-primary (ClusterIP 10.96.16.233, port sql/5432), so the name db-primary resolves to nothing and the probe loop in the init container never succeeds. The init container therefore stays Running and never exits 0, the pods stay Pending with Initialized=False and the api container stuck in PodInitializing, and the Deployment reports 0/2 Ready so the release never completes. The database tier itself is fine: the Service postgres-primary has one ready endpoint address, the pod postgres-primary-65b8877bbf-9z42x with db ready=True. Fix by setting DB_HOST in the billing-api pod template to postgres-primary (the Service name), then let the rollout replace the pods.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] The paged Deployment reports 0/2 Ready and both pods are Pending with the init container not ready.
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [symptom] The api container never starts because init has not completed.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > State:          Waiting
   >       Reason:       PodInitializing
3. [defect] The Deployment pod template hard-codes DB_HOST=db-primary for the wait-for-db init container.
   source: get_object({"kind": "deployment", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
4. [link] The init container is looping on that unresolvable host and never exits.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 50}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
5. [link] The only Service in the billing namespace is named postgres-primary, listening on 5432 - there is no Service named db-primary.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
6. [link] The pod is stuck at Initialized=False while wait-for-db is still Running with no restarts.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Initialized                 False

## Investigation ledger

- The database tier is down or the Service has no endpoints, so the wait would legitimately never succeed. — ruled out: The Service postgres-primary has one ready backing address pointing at the running postgres pod.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "name": "postgres-primary-65b8877bbf-9z42x",
- The scripts ConfigMap is missing the key the init container executes, so the container fails to run its wait script. — ruled out: ConfigMap billing-api-scripts contains both wait-for-db.sh and run.sh, and it mounts successfully.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ],
- Image pull failure or scheduling/capacity problem is holding the pods in Pending. — ruled out: The pod was scheduled and the image was already present; the init container started cleanly with no warning events.
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Normal  Started    21s   kubelet            spec.initContainers{wait-for-db}: Container started

## Verification recipe

1. `kubectl -n billing get deployment billing-api -o jsonpath='{.spec.template.spec.initContainers[0].env}'` — expect to see: "value": "db-primary"  [PRESENT]
2. `kubectl -n billing get svc` — expect to see: postgres-primary  [PRESENT]
3. `kubectl -n billing logs billing-api-ccb44c44c-89dn7 -c wait-for-db` — expect to see: waiting for db-primary:5432  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "billing",
    "name": "billing-api"
  },
  "mechanism": "The Deployment billing/billing-api sets .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value to \"db-primary\", but the database Service in the billing namespace is named postgres-primary, so that hostname resolves to nothing; the wait-for-db init container's TCP poll of db-primary:5432 never succeeds, it stays Running instead of exiting 0, and every replica the Deployment creates is held at Initialized=False with the api container in PodInitializing, leaving the Deployment at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
