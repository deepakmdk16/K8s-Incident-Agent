## Root cause

The billing-api Deployment's init container wait-for-db probes the hostname given in its DB_HOST environment variable, which is set to \"db-primary\". No Service by that name exists in the billing namespace — the database is fronted by Service billing/postgres-primary, which has one ready endpoint on port 5432. Because the name db-primary resolves to nothing, every connection attempt from the init script fails, the init container never exits 0, the pods stay Initialized=False with the api container held in PodInitializing, and the Deployment reports 0/2 Ready so the release cannot go live.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] Deployment billing-api is 0/2 ready with both pods stuck in init
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [symptom] The api container is blocked because init has not completed
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Reason:       PodInitializing
3. [defect] The init container is configured with DB_HOST=db-primary
   source: get_object({"kind": "deployment", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
4. [link] The init container's probe target is db-primary:5432 and it never succeeds
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7"}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
5. [link] The only Service in the billing namespace is postgres-primary; there is no db-primary Service for that name to resolve to
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
6. [link] The pod condition Initialized is False while the pod is scheduled and the DB_HOST value is db-primary
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Initialized                 False

## Investigation ledger

- The database tier is down, so waiting is legitimate — ruled out: The database Service has one ready pod address on port 5432, so a correctly named client would connect
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126",
- The scripts ConfigMap mount or a missing script key breaks the init container — ruled out: ConfigMap billing-api-scripts exists with both wait-for-db.sh and run.sh keys, and the init container ran that script and emitted log output
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ],
- Scheduling, image pull, or node capacity is blocking the pods — ruled out: The pod was scheduled and its init image was pulled and started, with no warning events recorded
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Container image "busybox:1.36" already present on machine

## Verification recipe

1. `kubectl -n billing get deploy billing-api -o jsonpath='{.spec.template.spec.initContainers[0].env}'` — expect to see: db-primary  [PRESENT]
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
  "mechanism": "Deployment billing/billing-api sets .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value to \"db-primary\", a hostname for which no Service object exists in the billing namespace, so the init container's connection attempts to db-primary:5432 fail on unresolvable DNS. The failed probe keeps the init container from exiting 0, so kubelet holds the pod at Initialized=False and refuses to start the api container (PodInitializing), and the Deployment is admitted 0 of 2 ready replicas.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
