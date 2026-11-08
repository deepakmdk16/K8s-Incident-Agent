# t2-yard-gate-release-blocked

**Tier:** T2, two-phase. **Status:** authored 2026-11-07; captured — see
"Capture" below. **Set:** `evals/scenarios-heldout/` (held-out root; scored by
`evals/scoring_v2.py`, class `pod-security-violation`, added for this set).
Authoring cluster `kind-incident-lab`, node image pinned to the frozen set's
digest (server v1.37.0).

**Namespaces owned:** `yard-ops` — which is also the gold object. No other
cluster-scoped objects; the namespace wipe is the reset.

## Informal ground truth

The platform team rolled Pod Security labels across the fleet's namespaces
this morning. `yard-ops` had been running at `warn: baseline` / `audit:
baseline` with its approved level recorded as baseline; the rollout set
`enforce: restricted` on it — a level the team had never warned at, let alone
approved. Nothing noticed until something created a pod. The yard-ops team's
release did: with maxSurge 0 / maxUnavailable 1 the rollout retired one old
pod, then the new ReplicaSet's first create was refused by the PodSecurity
admission plugin with the four restricted-profile violations the kiosk
template has always had. The page fires on the stalled release; the object
whose spec must change is the namespace itself.

## Why this case exists

The Namespace is captured (`cluster/namespaces.json`, since schema 1) but
**served by no tool**: `list_namespaces` renders names and pod counts, not
labels; `get_object` and `describe` refuse every cluster-scoped kind but the
two webhook kinds; the baseline's dump has no namespace object. The API
server's message tells every arm the mechanism in the rubric's words and
prescribes a pod-template fix, which is the designed pull toward the
Deployment. The case asks whether an arm can tell a namespace policy change
from a workload defect without being able to read the policy, and — for the
solution — whether the gate forces an honest verdict when the object it
needs is out of reach. Pre-registered predictions and the reading rule for a
`confirmed` Deployment row: `docs/experiments/2026-11-07-heldout-set.md`.

## Wait condition

`wait.sh` gates on all of: a ReplicaSet `FailedCreate` event in `yard-ops`
whose message contains `violates PodSecurity "restricted:latest"`;
`deployment/gate-checkin` at 0 updated replicas, 2 ready replicas, and
`Progressing=False` / `ProgressDeadlineExceeded` (progressDeadlineSeconds
120). `FailedCreate` alone is not decisive — quota, a missing ServiceAccount
and a failing webhook produce it too. An updated replica count of 1 or more
is reported as a race and fails the injection outright.

## Gold-side asymmetry

Objects an arm could name instead, and why each is indefensible in this
fixture:

- **`deployment/gate-checkin`** — the designed distractor, and what the
  admission message itself points at ("container kiosk must set …"). Its
  template did not change in any way admission looks at: only an env value
  differs between the ReplicaSets, and the old ReplicaSet's pods of the
  identical template are Running and Ready in the same namespace. Making
  the kiosk restricted-compliant would accommodate a label nobody approved:
  the namespace says `warn: baseline`, `audit: baseline`, approved level
  baseline — and `enforce: restricted`. No team enforces a stricter level
  than it warns at; the enforce value is the odd one out, and the
  remediation restores it to the level the other three fields already name.
- **the new ReplicaSet** — controller-owned; a human never edits it.
- **the Namespace's `enforce` label vs removing it** — both act on the same
  object, which is what the score compares; gold names `baseline` as
  canonical (the approved level) and removal as the interim mitigation.

The namespace's `metadata.name` is the paged namespace, so the token rule
for cluster-scoped gold (v2 README) is moot here: every arm can *name* the
object; none can *read* it. The signal is therefore the verdict and the
mechanism, and, for the LLM arms, whether the report reached the label set
at all.

## Determinism

Single-node kind, kindnet, no NetworkPolicy; only the node-cached
`busybox:1.36`. Pod Security admission is in-tree and enabled by default on
this API server; it is evaluated on every pod create with no cache. The
Namespace update in `fault.yaml` is synchronous and precedes the Deployment,
so the rollout's first create is always refused; the ReplicaSet controller
retries with back-off, so the `FailedCreate` **count** varies while its
presence does not. The Deployment's progress deadline is a fixed 120s from
the rollout's start. Existing pods are untouched by a label change (admission
is create-time only), which is what keeps 2 of 3 serving.

## Counterfactual verification (authoring contract rule 6)

Rehearsed live on 2026-11-08 (04:42–04:48 UTC) **before** the pristine
capture, on a separate `inject.sh --no-capture` injection:

| step | observed |
|---|---|
| fault live (wait.sh, ~3.5 min after apply) | `deployment/gate-checkin 2/3`, UP-TO-DATE 0; old ReplicaSet 2/2, new ReplicaSet desired 1 / current 0; namespace labels `audit=baseline, enforce=restricted, warn=baseline`; ten `FailedCreate` events on the new ReplicaSet (one combined `x7`), message exactly the PodSecurity text in `fault.yaml`'s header with container `kiosk` |
| `kubectl rollout undo` (the tempting fix) | does **not** recover: the old ReplicaSet goes to desired 3 / current 2 and starts collecting its own `FailedCreate` events (`x4` within 25s) — the same refusal, on the other ReplicaSet, because admission is per pod create and the label applies to both templates |
| undo again (back to the release) | 2/3, 0 updated, as before |
| applied `gold.json`'s `remediation_summary` verbatim (04:47:22 UTC) | `kubectl label namespace yard-ops pod-security.kubernetes.io/enforce=baseline --overwrite` → `labeled` |
| recovery | rollout completed within 19s (04:47:41 UTC): `3/3`, UP-TO-DATE 3, three new pods `Running` with `RELEASE_ID=2026.11.07`, old pods Terminating |

No object in `yard-ops` other than the Namespace was touched, which is what
`remediation_summary` claims. The cluster was then reset by `inject.sh`
(namespace wipe) and re-injected clean, through `setup.yaml` and the wait for
the healthy release, for the capture below.

A scratch probe earlier (2026-11-07, throwaway namespace, deleted
afterwards) established the exact admission message on this API server
version before the case was authored.

## Capture

`evals/inject.sh --id t2-yard-gate-release-blocked --root evals/scenarios-heldout`,
2026-11-08T04:50:46Z, server v1.37.0, capture schema 3: 278 files, 1.7M. The fault
manifested on the first clean injection with no race (0 updated replicas
throughout). Verified after the capture: `cluster/namespaces.json` carries
the four labels and the approved-level annotation on `yard-ops`;
`cluster/events.json` holds 10 ReplicaSet events with the PodSecurity
message; `ns/yard-ops/deployments.json` reports readyReplicas 2, no updated
replicas, conditions ['MinimumReplicasAvailable', 'FailedCreate', 'ProgressDeadlineExceeded']. The Deployment's
describe shows `ReplicaFailure True FailedCreate` without the message, so the
baseline's dump reaches the message only through the new ReplicaSet's row
(0 ready of 1 desired).
