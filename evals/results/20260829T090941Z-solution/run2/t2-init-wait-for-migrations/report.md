## Root cause

Deployment billing/billing-api gates its app container behind an init container named wait-for-db that polls the host given in the environment variable DB_HOST, which is set to "db-primary". No Service by that name exists in namespace billing; the only database Service there is Service billing/postgres-primary, which is healthy and has one endpoint address for pod billing/postgres-primary-65b8877bbf-9z42x, whose log says "postgres-primary: accepting connections on :5432". Because the name the init container is told to dial cannot resolve to that Service, the init script keeps printing "waiting for db-primary:5432" and never exits, so both billing-api pods sit at Initialized=False with the api container in Waiting/PodInitializing and the Deployment stays at 0/2 Ready, which is why the release is stalled and invoices queue unprocessed. Fixing the value to postgres-primary lets the init container connect and the rollout complete.

Remediation: edit Deployment billing/billing-api, field `.spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] The paged Deployment reports 0/2 Ready with both pods Pending, stuck in the wait-for-db init container.
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [symptom] The api container never starts because init has not completed.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > State:          Waiting
   >       Reason:       PodInitializing
3. [defect] The Deployment pod template hard-codes DB_HOST to db-primary for the init container.
   source: get_object({"kind": "deployment", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
4. [link] The init container is looping on that unresolvable host.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 40}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
5. [link] The only database Service in namespace billing is named postgres-primary; there is no db-primary Service.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
6. [link] The pod's effective environment confirms the wrong host name and that init is not Ready.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > DB_HOST:  db-primary

## Investigation ledger

- The database tier is actually down, so waiting is legitimate. — ruled out: The postgres pod is up and accepting connections on 5432.
  source: get_logs({"namespace": "billing", "pod": "postgres-primary-65b8877bbf-9z42x", "tail": 15}) — verified
  > postgres-primary: accepting connections on :5432
- The database Service has no endpoints, so nothing is reachable on 5432. — ruled out: Endpoints billing/postgres-primary has one ready address on port 5432.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126"
- The mounted ConfigMap is missing the script the init container runs, or the image cannot be pulled / pod cannot schedule. — ruled out: ConfigMap billing/billing-api-scripts contains wait-for-db.sh, and the pod scheduled and pulled its image without error.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ]

## Verification recipe

1. `kubectl -n billing get svc` — expect to see: postgres-primary  [PRESENT]
2. `kubectl -n billing get deploy billing-api -o yaml` — expect to see: "value": "db-primary"  [PRESENT]
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
  "mechanism": "Deployment billing/billing-api sets .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value to \"db-primary\", but the database Service in that namespace is Service billing/postgres-primary, so the name the init container dials does not resolve; the init script loops on \"waiting for db-primary:5432\" and never exits zero, leaving both pods with condition Initialized False and container api in State Waiting / Reason PodInitializing, so the Deployment reports ready=0/2.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
