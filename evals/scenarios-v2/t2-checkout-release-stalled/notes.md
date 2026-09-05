# t2-checkout-release-stalled

**Tier:** T2. **Status:** authored 2026-09-04; captured 2026-09-05; scored on all
three arms the same day (CHANGELOG [15]; bundles
`evals/results/20260905T0448*`; analysis in
`docs/experiments/2026-09-04-webhook-outage.md`). **Set:** `evals/scenarios-v2/`
(additive root; scored by `evals/scoring_v2.py`). Authoring cluster
`kind-incident-lab`, node image pinned to the frozen set's digest (server
v1.37.0).

**Namespaces owned:** `checkout`. **Cluster-scoped objects owned:**
`validatingwebhookconfiguration/workload-standards`, labelled
`incident-lab.dev/scenario: t2-checkout-release-stalled` so `inject.sh` can
delete it on the next reset.

## Informal ground truth

The platform team once ran a policy component behind Service
`policy-guard` in namespace `platform-policy`. Component and namespace are
gone; the `ValidatingWebhookConfiguration` that pointed at them was left
behind. It intercepts every pod CREATE outside the system namespaces with
`failurePolicy: Fail`, and because the API server cannot resolve the Service it
refuses every such create:

    Internal error occurred: failed calling webhook
    "validate.policy-guard.platform.internal": failed to call webhook:
    Post "https://policy-guard.platform-policy.svc:443/validate?timeout=10s":
    service "policy-guard" not found

Nothing notices until something creates a pod. The checkout team's release
does: with maxSurge 0 / maxUnavailable 1 the rollout retires one old pod, then
the new ReplicaSet's first create is refused, forever. The page fires on
`checkout`; the object whose spec must change lives in no namespace at all.

## Why this case exists

Both earlier attempts at headroom were presentational and did not bite
(CHANGELOG [13], [14]). Here the cause is **structurally** outside what either
LLM arm can read: cluster-scoped, absent from the baseline's dump policy by
construction, and refused by the solution's `get_object` by design. The fixture
carries the object (capture schema 2 added the webhook kinds), so the ceiling
being measured is the arms', not the benchmark's. Pre-registered predictions and
the decision rule: `docs/experiments/2026-09-04-webhook-outage.md`.

## Wait condition

`wait.sh` gates on all of: a ReplicaSet `FailedCreate` event in `checkout`
whose message names the webhook **and** says `service "policy-guard" not
found`; `deployment/checkout-api` at 0 updated replicas, 2 ready replicas, and
`Progressing=False` / `ProgressDeadlineExceeded` (progressDeadlineSeconds 120).
`FailedCreate` alone is not decisive — quota exhaustion and a missing
ServiceAccount also produce it. An updated replica count of 1 or more is
reported as a **race** (a pod admitted before the configuration registered) and
fails the injection outright rather than being waited out.

## Gold-side asymmetry

Objects an arm could name instead, and why each is indefensible in this
fixture:

- **`deployment/checkout-api`** — the designed distractor. Its spec is correct.
  What the fixture itself shows: the old ReplicaSet, with an identical template
  apart from one env value, created all three of its pods (`SuccessfulCreate`
  x3 in its describe) seconds before the new one was refused — the template is
  not what changed. That a rollback does not recover (the old ReplicaSet then
  needs to create a pod and is refused the same way) is rehearsal evidence
  only, recorded below; no snapshot can carry a counterfactual.
- **the new ReplicaSet** — controller-owned; a human never edits it.
- **the missing Service / namespace** — there is no object to edit, only an
  entire policy component to reinstall, and the scored answer must name an
  object that exists in the snapshot.
- **`failurePolicy: Ignore` vs delete** — both act on the same object, which is
  what the score compares; gold's remediation names delete as canonical and
  Ignore as the interim mitigation.

The configuration's `metadata.name` (`workload-standards`) deliberately shares
**no token** with the webhook name the error message prints
(`validate.policy-guard.platform.internal`), the Service (`policy-guard`) or its
namespace (`platform-policy`), so no arm can derive the object's name from the
event text — it has to read the configuration to name it. That is realistic:
a chart's release name and its webhook's DNS-style name routinely differ
(cert-manager, Kyverno, Gatekeeper all do). The object carries the metadata a
Helm orphan really has — `app.kubernetes.io/managed-by: Helm` and the
`meta.helm.sh/release-namespace: platform-policy` annotation naming the
namespace that is gone. The pipeline's own `incident-lab.dev/scenario` label is
scrubbed out by `capture.sh` and gated by `checkpoints.sh`, so nothing in the
fixture marks the object as planted.

`namespaceSelector` excludes only the system namespaces; `default` is
intentionally not excluded (inject.sh only reads it, and a real orphan would
not exclude it either).

## Determinism

Single-node kind, kindnet, no NetworkPolicy. Only the node-cached
`busybox:1.36`. Admission is evaluated on every create with no cache; the
ReplicaSet controller retries with backoff, so the `FailedCreate` **count**
varies while its presence does not (wait.sh gates on presence and message). The
Deployment's progress deadline is a fixed 120s from the rollout's start.
`namespaceSelector` excludes `kube-system`, `kube-public`, `kube-node-lease`
and `local-path-storage`, so nothing the control plane or the storage
provisioner creates is affected during capture.

## Counterfactual verification (authoring contract rule 6)

Rehearsed live on 2026-09-04 (15:36–15:40 UTC) **before** the pristine
capture, on a separate `inject.sh --no-capture` injection:

| step | observed |
|---|---|
| fault live (wait.sh, ~2 min after apply) | `deployment/checkout-api 2/3`, UP-TO-DATE 0; old ReplicaSet 2/2, new ReplicaSet desired 1 / current 0; conditions `Available=True`, `ReplicaFailure=True FailedCreate`, `Progressing=False ProgressDeadlineExceeded`; FailedCreate message exactly the text quoted above |
| `kubectl rollout undo` (the tempting fix) | does **not** recover: the old ReplicaSet goes to desired 3 / current 2 and starts collecting its own `FailedCreate` events (count 13 within 25s) — the same refusal, on the other ReplicaSet |
| undo again (back to the release) | 2/3, 0 updated, as before |
| applied `gold.json`'s `remediation_summary` verbatim | `kubectl delete validatingwebhookconfiguration workload-standards` → `deleted` |
| recovery | rollout completed within 54s: `3/3`, UP-TO-DATE 3, `Progressing=True NewReplicaSetAvailable`; old pods Terminating, three new pods Running |

No object in `checkout` was touched, which is what `remediation_summary`
claims. The cluster was then reset by `inject.sh` (the labelled configuration
had already been deleted by the remediation; the namespaced half wiped
`checkout`) and re-injected clean for the capture below.

A scratch probe earlier the same day (throwaway namespace, throwaway
configuration, deleted afterwards) established the exact error text on this
API server version and the same steady state before the case was authored.

## Capture

`evals/inject.sh --id t2-checkout-release-stalled --root evals/scenarios-v2`,
2026-09-05 04:48:02 UTC, server v1.37.0, **capture schema 2** (the first
fixture to carry `cluster/validatingwebhookconfigurations.json`,
`cluster/mutatingwebhookconfigurations.json` and the configuration's describe
under `cluster/describe/`). 275 files, 1.7 MB. The fault manifested on the first
injection with no race (0 updated replicas throughout). The decisive text is in
`cluster/events.json` and in the new ReplicaSet's describe; the Deployment's
describe shows `ReplicaFailure True FailedCreate` without the message, so the
baseline's dump reaches the message only through the ReplicaSet row. The API
server's own log in `kube-system` carries the webhook name on every refused
create ("failing closed"), never the configuration's name.

Two earlier captures of this case (2026-09-04, 15:44 UTC and one aborted by
the inject lint) were discarded before anything was committed: the first
carried the pipeline label unscrubbed and a configuration name that shared
tokens with the event text, both found by the design review; the fixture here
is the only one that ever existed in the tree. Verified after this capture:
`grep -rl workload-standards evals/fixtures/t2-checkout-release-stalled/ns/`
returns nothing, and `grep -rn 'incident-lab\.dev/'` over the fixture returns
nothing.
