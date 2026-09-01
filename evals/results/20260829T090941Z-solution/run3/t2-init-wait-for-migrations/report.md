## Root cause

Deployment billing/billing-api gates every pod behind an init container "wait-for-db" that polls the host given in env DB_HOST, which is set to "db-primary". No such Service exists in namespace billing: the only Service is billing/postgres-primary (ClusterIP 10.96.16.233, port 5432), and it is healthy with one endpoint address backed by pod billing/postgres-primary-65b8877bbf-9z42x. Because the name the init container dials never resolves to the database, the init container keeps printing "waiting for db-primary:5432" and never exits 0, so the "api" container stays in Waiting/PodInitializing, the pods stay Pending, and the deployment reports 0/2 Ready, which is why the release watchdog paged and invoices queue unprocessed. Fixing the env value to the real Service name postgres-primary lets the init container complete and the rollout finish.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] The paged deployment reports 0/2 ready and both pods are Pending with the init container not ready.
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [defect] The init container's DB_HOST env value is db-primary.
   source: get_object({"kind": "deployment", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
3. [link] The init container is stuck polling that hostname.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 40}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
4. [link] The only Service in namespace billing is postgres-primary on port 5432, not db-primary.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
5. [link] Because the init container never completes, the app container cannot start.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > State:          Waiting
   >     Reason:       PodInitializing
6. [link] The pod is not Initialized, confirming the gate is the init container.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Initialized                 False

## Investigation ledger

- The database tier is actually down, so waiting is legitimate. — ruled out: Service billing/postgres-primary has one ready endpoint address backed by the running postgres pod, so the database is reachable under its real name.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126",
  >             "nodeName": "incident-lab-control-plane",
  >             "targetRef": {
  >               "kind": "Pod",
  >               "name": "postgres-primary-65b8877bbf-9z42x"
- The mounted script ConfigMap is missing the wait-for-db.sh or run.sh key, so the containers fail on a missing file. — ruled out: ConfigMap billing/billing-api-scripts contains both keys the containers execute.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ]
- Pods cannot be scheduled or the image cannot be pulled. — ruled out: The pod was scheduled and the image was already present; the init container started cleanly with zero restarts.
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Normal  Started    21s   kubelet            spec.initContainers{wait-for-db}: Container started

## Verification recipe

1. `kubectl -n billing get deploy billing-api -o jsonpath='{.spec.template.spec.initContainers[0].env}'` — expect to see: "value": "db-primary"  [PRESENT]
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
  "mechanism": "In Deployment billing/billing-api, .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value is \"db-primary\", but the database Service in that namespace is billing/postgres-primary; the name has no Service and does not resolve, so the init container logs \"waiting for db-primary:5432\" every five seconds and never exits, leaving the pod condition Initialized False and container api in State Waiting / Reason: PodInitializing, so the Deployment stays at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
