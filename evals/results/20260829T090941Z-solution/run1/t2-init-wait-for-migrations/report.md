## Root cause

Deployment billing/billing-api gates its application container behind an init container named wait-for-db, which polls the host given in its environment variable DB_HOST. That variable is set to "db-primary", but the only database Service in the namespace is Service billing/postgres-primary (ClusterIP 10.96.16.233, port 5432), and its Endpoints already carry one ready address, 10.244.0.126, pointing at pod postgres-primary-65b8877bbf-9z42x. Because no Service named db-primary exists in namespace billing, the name db-primary never resolves, the init container keeps printing "waiting for db-primary:5432" and never exits, so both replicas sit with Initialized False and the api container Waiting with Reason PodInitializing. The deployment therefore reports 0/2 Ready and the release cannot go live, even though the database tier is healthy. The fix is to point the init container at the Service that actually exists: set DB_HOST to postgres-primary in the deployment's pod template.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] The paged deployment reports 0/2 Ready with both pods stuck in the init container.
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [symptom] Both billing-api pods are Pending with the api container blocked on PodInitializing.
   source: namespace_overview(billing) — verified
   > pod/billing-api-ccb44c44c-89dn7 phase=Pending labels={app=billing-api, pod-template-hash=ccb44c44c} node=incident-lab-control-plane init:wait-for-db(ready=False,restarts=0) api(ready=False,restarts=0,waiting=PodInitializing)
3. [defect] The deployment's init container hardcodes DB_HOST to db-primary.
   source: get_object({"kind": "deployments", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
4. [link] The init container is polling db-primary:5432 and never succeeding.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 40}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
5. [link] The wait loop repeats without ever completing.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-m756m", "tail": 10}) — verified
   > waiting for db-primary:5432
6. [defect] The only Service in namespace billing is postgres-primary; there is no Service named db-primary for the name to resolve to.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
7. [link] The pod is stuck with Initialized False and the api container waiting on PodInitializing, with DB_HOST db-primary in its environment.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Initialized                 False
8. [link] The running pod's environment confirms the bad hostname reaching the container.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Environment:
   >       DB_HOST:  db-primary

## Investigation ledger

- The database tier is down or not serving, so the wait is legitimate. — ruled out: Service billing/postgres-primary has one ready endpoint address on port 5432 backed by the running database pod, so the database is reachable at its real name.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126",
  >           "nodeName": "incident-lab-control-plane",
  >           "targetRef": {
  >             "kind": "Pod",
  >             "name": "postgres-primary-65b8877bbf-9z42x"
- The Service selector is wrong so nothing backs the database Service. — ruled out: The Service selector app=postgres-primary matches the running database pod's labels and the overview shows one endpoint address, so selector matching is intact.
  source: namespace_overview(billing) — verified
  > service/postgres-primary selector={app=postgres-primary} endpointAddresses=1
- The scripts ConfigMap is missing the key the init container runs, or the volume fails to mount. — ruled out: ConfigMap billing/billing-api-scripts contains both wait-for-db.sh and run.sh, and the init container started and produced log output, so the mount and key resolve.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ]
- Image pull failure or scheduling/capacity problem is blocking the pods. — ruled out: The pod scheduled successfully and its image was already present and the init container started, with no warning events.
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Normal  Started    21s   kubelet            spec.initContainers{wait-for-db}: Container started

## Verification recipe

1. `kubectl -n billing get deployment billing-api -o jsonpath='{.spec.template.spec.initContainers[0].env}'` — expect to see: db-primary  [PRESENT]
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
  "mechanism": "Deployment billing/billing-api sets .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value to \"db-primary\", but the only database Service in the namespace is Service billing/postgres-primary, so that hostname does not resolve. The wait-for-db init container's TCP poll of db-primary:5432 never succeeds \u2014 it logs \"waiting for db-primary:5432\" every five seconds and never exits 0 \u2014 leaving both pods with condition \"Initialized  False\" and the api container \"Reason:       PodInitializing\", so the deployment stays at ready=0/2.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
