# t3-driver-login-failures

**Tier:** T3 (the held-out set's hard case). **Status:** authored 2026-11-07;
captured — see "Capture" below. **Set:** `evals/scenarios-heldout/` (held-out
root; scored by `evals/scoring_v2.py`). Authoring cluster `kind-incident-lab`,
node image pinned to the frozen set's digest (server v1.37.0). Captured under
the held-out noise pack (`../_noise/`: 20 healthy namespaces + decoys
`trailer-pool`, `fuel-tax`).

**Namespaces owned:** `driver-portal`, plus the noise pack's 22 (listed in
`../_noise/namespaces.txt` and `../_noise/decoys.txt`). No cluster-scoped
objects.

## Informal ground truth

The driver mobile app's sign-in service, `driver-auth`, validates sessions
against the identity provider's signing keys and refreshes the provider
configuration from a ConfigMap through the API every ten seconds. The
RoleBinding meant to let it do that names the ServiceAccount
`driver-auth-svc`; the account is `driver-auth`; no account named
`driver-auth-svc` exists. Every refresh is denied with 403, the keys go
stale, and the service rejects every session issued after the last key
rotation — about 60% of drivers at start of shift. The pod is Running and
Ready, emits no events, and has not been redeployed.

In the same namespace, `badge-photo-resizer` (the ID-card photo pipeline)
crash-loops loudly on an unset `PHOTO_STORE_URL`. The noise pack adds a
Pending `pool-balancer` and a failing `ifta-filer` CronJob elsewhere. None
of them touches sign-in.

## Why this case exists

The frozen T3s put the loud decoy in *another* namespace, and the rules arm
— which only looks at the paged namespace — solved both. Here the decoy is in
the paged namespace, so a precedence-ordered analyzer picks it; the rbac
analyzer stays silent because no binding names the pod's real account. The
frozen rbac analog (`t2-rbac-sync-forbidden`) is the one frozen case the
baseline fails (0/3 resource) and the rules arm fails; this is that atom
with a decoy on top, in vocabulary the arms have never seen. Pre-registered
predictions: `docs/experiments/2026-11-07-heldout-set.md`.

## Wait condition

`wait.sh` gates on all of: the driver-auth log containing both
`discovery=ok` and `403 Forbidden`; the driver-auth pod `Ready=True`;
`badge-photo-resizer` with `restartCount >= 3` in `CrashLoopBackOff`;
`trailer-pool/pool-balancer` Pending with reason `Unschedulable`; at least
one Failed job in `fuel-tax`. The noise pack's `namespaces.txt` lists the
healthy namespaces only, so `inject.sh` never blocks on a decoy that is by
construction never Available.

## Gold-side asymmetry

- **`rolebinding/driver-auth-oidc-reader`** (gold) — its subject names an
  account that does not exist. Because the typo'd subject is nonexistent, the
  binding is the only defensible fix: changing the pod's `serviceAccountName`
  to match the binding would point the workload at an account that is not
  there (red-team rule from the frozen case: a binding naming a *real* other
  account would make the Deployment an equally valid fix and exact-match gold
  a coin flip).
- **`deployment/driver-auth`** — correct: the right account, the right
  ConfigMap name, a token that works (the discovery probe succeeds in the
  same log, which rules out a mis-sent header).
- **`role/oidc-config-reader`** — grants exactly what is needed.
- **`serviceaccount/driver-auth`** — exists; creating a second account named
  `driver-auth-svc` would "fix" the symptom by duplicating identities rather
  than correcting the one wrong field.
- **`deployment/badge-photo-resizer`** — the loud decoy. Genuinely broken,
  conditional (setting `PHOTO_STORE_URL` fixes it), and verifiably unrelated:
  it serves the badge printer, never sign-in, and the sign-in service's own
  log names its defect.

## Determinism

Single-node kind, kindnet, no NetworkPolicy; only the node-cached
`busybox:1.36`. RBAC is evaluated on every request with no cache; the 403
appears on the first poll and every poll after. The decoy's crash-loop
back-off timing varies, the restart count only grows, and wait.sh gates on a
floor. The noise pack is a pure function of its generator; its decoys are
unschedulable by a request no host satisfies and a file that is never
mounted.

**First injection (2026-11-08, 04:52 UTC) did not manifest and was stopped
at the wait:** the decoy's original command exited within milliseconds, and
this kubelet (v1.37.0) then reports the container as `terminated/Error`
through every back-off — the pod never showed `CrashLoopBackOff` (restarts
climbing 3, 4 with `STATUS Error`), so the wait condition keyed on that
reason could not be met, and a fixture captured in that state would have
silenced any analyzer keyed on `CrashLoopBackOff` by an accident of timing.
The route-optimizer in `t2-route-planning-degraded`, whose process runs ~3s
before exiting, reached `CrashLoopBackOff` every time, so the second
injection gave the decoy a 3s wait before giving up (kept: still conditional,
rule 1 holds) — and the status **still** read `terminated/Error` between
restarts. The status string is therefore not this decoy's decisive evidence;
the third injection's `wait.sh` gates on what is deterministic — restart
count climbing past 3, the container not Ready, exit code 1 or a
CrashLoopBackOff wait, and the decoy's own `PHOTO_STORE_URL still not set`
log line — and the capture records whichever status string the kubelet
happened to be showing. Consequence for the rules arm, stated before its run:
its crashloop analyzer keys on the `CrashLoopBackOff` waiting reason, so if
the capture caught the decoy as `terminated/Error` the analyzer is silent,
no analyzer fires, and the arm answers `inconclusive` instead of the
predicted confident wrong name. Either outcome is read against the
prediction honestly; the fault itself manifested identically on every
injection.

## Counterfactual verification (authoring contract rule 6)

Rehearsed live on 2026-11-08 (04:52–04:59 UTC) **before** the pristine
capture, on an `inject.sh --no-capture` injection (the second; see
"Determinism" for why the first and second waits were stopped and the wait
condition re-keyed — the fault itself manifested identically on each):

| step | observed |
|---|---|
| fault live (corrected wait.sh, run against the live injection) | `driver-auth 1/1 Running`, 0 restarts, log cycling `identity=driver-auth discovery=ok` / `provider config fetch error: wget: server returned error: HTTP/1.1 403 Forbidden` / `login validation degraded: signing keys stale, rejecting sessions issued after the last key rotation`; `rolebinding/driver-auth-oidc-reader` subject `ServiceAccount/driver-auth-svc`, roleRef `oidc-config-reader`; the namespace's ServiceAccounts are `default` and `driver-auth` only; decoy `badge-photo-resizer 0/1`, 4 restarts, exit code 1, one `BackOff` Warning event; `trailer-pool/pool-balancer` Unschedulable; `fuel-tax` with 2 failed job runs |
| applied `gold.json`'s `remediation_summary` verbatim (04:58:39 UTC) | `kubectl patch rolebinding driver-auth-oidc-reader -n driver-portal --type json -p '[{"op":"replace","path":"/subjects/0/name","value":"driver-auth"}]'` → `patched` |
| recovery | on the next 10s poll (04:58:53 UTC) the log read `provider config refreshed from oidc-provider-config; validating sessions against current signing keys`; `driver-auth` restarts still 0 — no pod, Deployment, Role or ServiceAccount was touched |

The cluster was then reset by `inject.sh` (namespace wipe of the scenario and
the 22 noise namespaces) and re-injected clean, noise pack first, for the
capture below.

## Capture

`evals/inject.sh --id t3-driver-login-failures --root evals/scenarios-heldout`,
2026-11-08T05:02:03Z, server v1.37.0, capture schema 3, under the held-out noise pack:
993 files, 4.8M, 28 namespaces. The fault manifested on the first
clean injection with the corrected wait (decoy at 3 restarts when
wait.sh returned). Verified after the capture: `rolebindings.json` carries
subject `driver-auth-svc`; the driver-auth log holds 7 `403 Forbidden` lines and
7 `discovery=ok` lines from a pod with `ready=true` and
0 restarts; the decoy pod is `ready=false` with
3 restarts and its container state at capture is
**`terminated/Error (exit 1)`** — the fact the design doc's second amendment says
decides the rules arm's row on this case; no pipeline label anywhere in the
fixture.
