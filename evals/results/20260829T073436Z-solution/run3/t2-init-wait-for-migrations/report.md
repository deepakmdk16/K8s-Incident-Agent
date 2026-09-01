## Root cause

The Deployment billing/billing-api gates its application container behind an init container "wait-for-db" that polls the host named in its DB_HOST environment variable on port 5432. That variable is set to "db-primary", but no Service by that name exists in the billing namespace; the only database Service is postgres-primary (ClusterIP 10.96.16.233, port 5432), and it is healthy with one ready endpoint address backed by pod postgres-primary-65b8877bbf-9z42x. Because the name db-primary does not resolve, every probe the init container makes fails and it stays in its retry loop printing "waiting for db-primary:5432", so both replicas remain Pending with Initialized=False and the api container is held in PodInitializing — the Deployment reports 0/2 Ready and no new version serves invoices. The fix is a one-line edit: point the init container's DB_HOST at postgres-primary, the Service that actually exists.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] The paged Deployment reports 0/2 Ready with both pods Pending and blocked in their init container.
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [symptom] Both billing-api pods are stuck with init container not ready and app container in PodInitializing.
   source: namespace_overview(billing) — verified
   > pod/billing-api-ccb44c44c-89dn7 phase=Pending labels={app=billing-api, pod-template-hash=ccb44c44c} node=incident-lab-control-plane init:wait-for-db(ready=False,restarts=0) api(ready=False,restarts=0,waiting=PodInitializing)
3. [defect] The Deployment pod template sets DB_HOST to db-primary for the wait-for-db init container.
   source: get_object({"kind": "deployments", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
4. [link] The only Service in the billing namespace is postgres-primary on port 5432; no Service named db-primary exists.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
5. [link] The init container is repeatedly failing to reach the unresolvable host name.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 40}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
6. [link] The pod describe shows the init container Running with DB_HOST=db-primary and the pod Initialized=False.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Environment:
   >       DB_HOST:  db-primary

## Investigation ledger

- The database tier itself is down, so the wait loop is legitimately waiting. — ruled out: The Service postgres-primary has one ready endpoint address backed by the running database pod, so the database is serving.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126",
  >             "nodeName": "incident-lab-control-plane",
  >             "targetRef": {
  >               "kind": "Pod",
  >               "name": "postgres-primary-65b8877bbf-9z42x"
- A missing ConfigMap or missing script key prevents the init container from running. — ruled out: The ConfigMap billing-api-scripts exists and contains both wait-for-db.sh and run.sh, and the init container started successfully.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ],
- The pods cannot be scheduled or the image cannot be pulled. — ruled out: Events show the pod scheduled and the init container image pulled, created and started with no warnings.
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Normal  Started    21s   kubelet            spec.initContainers{wait-for-db}: Container started

## Verification recipe

1. `kubectl -n billing get deployment billing-api -o jsonpath='{.spec.template.spec.initContainers[0].env}'` — expect to see: db-primary  [PRESENT]
2. `kubectl -n billing get svc` — expect to see: postgres-primary  [PRESENT]
3. `kubectl -n billing logs billing-api-ccb44c44c-89dn7 -c wait-for-db --tail=5` — expect to see: waiting for db-primary:5432  [PRESENT]
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
  "mechanism": "In the Deployment billing/billing-api, .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value is \"db-primary\" instead of \"postgres-primary\", the only database Service that exists in the billing namespace. Each connection attempt the init container makes to db-primary:5432 fails because that name has no Service to resolve to, so the init container remains in its retry loop emitting \"waiting for db-primary:5432\", the pods are held at Initialized=False with the api container blocked in PodInitializing, and the Deployment sits at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
