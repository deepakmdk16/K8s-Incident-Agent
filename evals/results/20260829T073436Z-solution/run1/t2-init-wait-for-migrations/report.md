## Root cause

The Deployment billing/billing-api gates startup on an init container named wait-for-db that polls $DB_HOST:5432. Its pod template sets env DB_HOST to "db-primary", but the only Service in the billing namespace fronting the database is postgres-primary (ClusterIP 10.96.16.233, port 5432), whose Endpoints already hold one ready address (10.244.0.126, pod postgres-primary-65b8877bbf-9z42x). The hostname db-primary resolves to nothing, so the init container keeps logging "waiting for db-primary:5432" and never exits; both billing-api pods stay Pending with Initialized=False and the api container stuck in PodInitializing, leaving the Deployment at 0/2 Ready. Fix: set the DB_HOST value in the Deployment pod template to postgres-primary (the actual Service name) and let the rollout replace the pods.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] Deployment billing-api reports 0/2 ready and both pods are Pending with the init container not ready.
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [defect] The Deployment pod template hardcodes DB_HOST=db-primary for the wait-for-db init container.
   source: get_object({"kind": "deployment", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
3. [link] The init container is looping on that hostname and never completing.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7"}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
4. [link] The only Service in the billing namespace is postgres-primary; no Service named db-primary exists.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
5. [link] The postgres-primary Service has a ready backing address on port 5432, so the database tier is reachable under its real name.
   source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
   > "ip": "10.244.0.126"
6. [symptom] The pod is blocked at initialization, with the app container waiting on PodInitializing.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Initialized                 False

## Investigation ledger

- The database tier itself is down, so waiting is legitimate — ruled out: The postgres-primary Deployment is 1/1 ready and its pod's db container is ready.
  source: namespace_overview(billing) — verified
  > pod/postgres-primary-65b8877bbf-9z42x phase=Running labels={app=postgres-primary, pod-template-hash=65b8877bbf} node=incident-lab-control-plane db(ready=True,restarts=0)
- The database Service selector matches nothing, so its Endpoints are empty — ruled out: The Endpoints object for postgres-primary contains one address targeting the running postgres pod.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "name": "postgres-primary-65b8877bbf-9z42x"
- A missing ConfigMap or missing script key prevents the init container from running — ruled out: ConfigMap billing-api-scripts exists with both run.sh and wait-for-db.sh, and the init container started successfully.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ]
- Image pull or scheduling failure is blocking the pods — ruled out: The pod is scheduled and the image was already present; the init container was created and started.
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Normal  Started    21s   kubelet            spec.initContainers{wait-for-db}: Container started

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
  "mechanism": "In the Deployment billing/billing-api, .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value is \"db-primary\" instead of \"postgres-primary\", the name of the database Service that exists in the billing namespace; the init container's TCP poll of db-primary:5432 never resolves, so it stays Running and never exits, the pods it gates report Initialized=False with the api container waiting on PodInitializing, and the Deployment stays at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
