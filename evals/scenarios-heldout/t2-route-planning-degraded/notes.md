# t2-route-planning-degraded

**Tier:** T2. **Status:** authored 2026-11-07; captured — see "Capture" below.
**Set:** `evals/scenarios-heldout/` (held-out root; scored by
`evals/scoring_v2.py`). Authoring cluster `kind-incident-lab`, node image
pinned to the frozen set's digest (server v1.37.0). **Capture schema 3** — the
first fixture to carry `cluster/customresourcedefinitions.json` and a
`ns/dispatch/crs/` directory.

**Namespaces owned:** `dispatch`. **Cluster-scoped objects owned:**
`customresourcedefinition/routeprofiles.fleet.northbound.io`, labelled
`incident-lab.dev/scenario: t2-route-planning-degraded` so `inject.sh` deletes
it on the next reset (deleting the CRD deletes its custom resources).

## Informal ground truth

The dispatch team configures its route optimizer through a custom resource,
`RouteProfile`: region, stop cap, departure window, and the solver the
optimizer must connect to. Someone edited the weekday profile and typed the
solver host as `route-solvr`. The optimizer reads its profile at startup
through the API, checks it can reach the solver, cannot, and exits; kubelet
restarts it; 0/2 Ready; the planning screen falls back to yesterday's routes.
The solver and its Service are fine — `eta-publisher`, the other consumer,
reaches `route-solver.dispatch.svc:7000` every twenty seconds and says so in
its log.

## Why this case exists

The object whose spec must change is of a kind **no tool knows**:
`solution/fixture.py` NAMESPACED_KINDS is a fixed list, `get all -A` lists no
custom resources, and the baseline's dump policy has no path to one. Every
served object is healthy. An arm can *name* the object — the Deployment's
env names the profile, the mounted script shows the API path, the log shows
the value — but cannot *read* it, so the validator's anchoring requirement
(V7: a real result about the failing object) is unearnable and a `confirmed`
verdict should be too. Found while wiring (2026-11-08): the gate refuses
earlier than that — V4 EXISTS reads a kind no reader knows as "does not
resolve" and rejects the name at *any* verdict, so today the solution arm
cannot submit this object at all. Disclosed, with the revised prediction, in
the design doc's amendment; pinned in the adversarial suite. The class is the frozen `app-crashloop`; the case's
signal is `resource_correct` and the verdict. Pre-registered predictions and
the reading rule: `docs/experiments/2026-11-07-heldout-set.md`.

The fixture carries the object (capture schema 3), so the ceiling being
measured is the arms', not the benchmark's; a later slice that opens CRD
discovery (roadmap 2.7) re-scores this case without a re-capture.

## Wait condition

`wait.sh` gates on all of: both optimizer pods with `restartCount >= 3` and a
`CrashLoopBackOff` wait; the optimizer log line
`solver route-solvr.dispatch.svc:7000 unreachable`; and the eta-publisher log
line `solver route-solver.dispatch.svc:7000 reachable`. A restart count alone
is not decisive (one bad start looks the same), and the healthy consumer's
line is part of the evidence the fixture must hold.

## Gold-side asymmetry

Objects an arm could name instead, and why each is indefensible here:

- **`deployment/route-optimizer`** — the designed distractor and what every
  pod-status analyzer points at. Its spec is correct: the profile name it
  loads exists, its ServiceAccount is bound to read it (the log shows the
  profile loaded, then the endpoint check failing — not a 403), and the
  endpoint is not in the Deployment at all. "Hard-code the right endpoint in
  the Deployment" would change the design, not fix the defect.
- **`service/route-solver`** — healthy, with ready endpoints, and reached by
  `eta-publisher` on the same host:port throughout. Renaming a working
  Service to match a typo would break its other consumer.
- **`configmap/route-optimizer-entrypoint`** — the script does what it says;
  it reads the endpoint from the profile and reports it. Nothing in it names
  the wrong host.
- **the RBAC triple** — correct; the profile is read successfully (its value
  appears in the log).

The custom kind's spellings (`RouteProfile`, `routeprofiles`,
`routeprofile.fleet.northbound.io`) are aliased to one identity in
`evals/scoring_v2.py` so the score compares the object, not the plural an arm
writes.

## Determinism

Single-node kind, kindnet, no NetworkPolicy; only the node-cached
`busybox:1.36`. `route-solvr.dispatch.svc` never resolves, so `nc -z` fails
in under a second every time; the back-off timing varies, the restart count
only grows, and wait.sh gates on a floor. The CRD is applied in `setup.yaml`
and is Established long before `fault.yaml` runs (inject waits for the setup
Deployments to be Available first), so the RouteProfile create never races
its CRD. The script tolerates both the pretty-printed and the compact JSON
the API server may return (`tr -d '\n'` before the `sed`).

## Counterfactual verification (authoring contract rule 6)

Rehearsed live on 2026-11-08 (04:33–04:36 UTC) **before** the pristine
capture, on a separate `inject.sh --no-capture` injection:

| step | observed |
|---|---|
| fault live (wait.sh, ~70s after apply) | both optimizer pods `0/1 CrashLoopBackOff`, 3 restarts each; log `profile weekday-routing: solverEndpoint=route-solvr.dispatch.svc:7000` then `solver route-solvr.dispatch.svc:7000 unreachable (RouteProfile weekday-routing spec.solverEndpoint); cannot plan routes, exiting`; `kubectl get routeprofiles` column SOLVER `route-solvr.dispatch.svc:7000`; eta-publisher and route-solver `1/1 Running` |
| applied `gold.json`'s `remediation_summary` verbatim | `kubectl patch routeprofile weekday-routing -n dispatch --type merge -p '{"spec":{"solverEndpoint":"route-solver.dispatch.svc:7000"}}'` → `patched` |
| recovery | no pod was deleted or restarted by hand: on their next back-off restart (restart count 3 → 4) both pods logged `solverEndpoint=route-solver.dispatch.svc:7000` and `connected to solver route-solver.dispatch.svc:7000; serving route plans on :8080`, passed the readiness probe, and the Deployment reported `2/2 ready` within 31s of the patch (04:34:58 → 04:35:29 UTC) |

No object in the Deployment, the Service or the solver was touched, which is
what `remediation_summary` claims. The cluster was then reset by `inject.sh`
(it deleted the labelled CRD first, then wiped the namespace) and re-injected
clean for the capture below.

A scratch probe earlier (2026-11-07, throwaway namespace and a throwaway
CRD carrying a scratch scenario label, both deleted afterwards) established
that the API server returns the custom resource pretty-printed to a plain
HTTP client, which is why the entrypoint joins lines before matching the
field; the first probe's regex (no space after the colon) matched nothing.

## Capture

`evals/inject.sh --id t2-route-planning-degraded --root evals/scenarios-heldout`,
2026-11-08T04:37:16Z, server v1.37.0, **capture schema 3**: 303 files, 1.8M. The
fault manifested on the first clean injection (3 restarts on both pods when
wait.sh returned). The custom kind is in the fixture at
`cluster/customresourcedefinitions.json`,
`cluster/describe/customresourcedefinition.apiextensions.k8s.io_routeprofiles.fleet.northbound.io.txt`,
`ns/dispatch/crs/routeprofiles.fleet.northbound.io.json` (the object, with
the typo'd endpoint) and
`ns/dispatch/describe/routeprofile.fleet.northbound.io_weekday-routing.txt`.
Verified after the capture: no file under the fixture contains the pipeline
label `incident-lab.dev/`; the name `weekday-routing` appears in exactly
these files — `ns/dispatch/crs/routeprofiles.fleet.northbound.io.json`, `ns/dispatch/deployments.json`, `ns/dispatch/describe/deployment.apps_route-optimizer.txt`, `ns/dispatch/describe/pod_route-optimizer-78fc6747d7-22sfp.txt`, `ns/dispatch/describe/pod_route-optimizer-78fc6747d7-mqwkc.txt`, `ns/dispatch/describe/replicaset.apps_route-optimizer-78fc6747d7.txt`, `ns/dispatch/describe/routeprofile.fleet.northbound.io_weekday-routing.txt`, `ns/dispatch/logs/route-optimizer-78fc6747d7-22sfp__optimizer.log`, `ns/dispatch/logs/route-optimizer-78fc6747d7-22sfp__optimizer.previous.log`, `ns/dispatch/logs/route-optimizer-78fc6747d7-mqwkc__optimizer.log`, `ns/dispatch/logs/route-optimizer-78fc6747d7-mqwkc__optimizer.previous.log`, `ns/dispatch/pods.json`, `ns/dispatch/replicasets.json` — so an arm can name the
object from served reads (the Deployment's env, the entrypoint ConfigMap,
the optimizer logs and events) and read it from none of them.
