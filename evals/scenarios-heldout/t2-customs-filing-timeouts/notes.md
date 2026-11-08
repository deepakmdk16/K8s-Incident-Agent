# t2-customs-filing-timeouts

**Tier:** T2. **Status:** authored 2026-11-07; captured — see "Capture" below.
**Set:** `evals/scenarios-heldout/` (held-out root; scored by
`evals/scoring_v2.py`). Authoring cluster `kind-incident-lab`, node image
pinned to the frozen set's digest (server v1.37.0).

**Namespaces owned:** `customs-docs`. No cluster-scoped objects.

## Informal ground truth

The customs-docs team's e-manifest pipeline has two parts: the renderer
(two httpd pods behind a Service) and the submitter, which renders a filing
through that Service and sends it to the border agency. Someone re-applied
the renderer's Service with the selector value `manifest-render` — one
syllable short of the pods' `manifest-renderer` label. The Service selects
nothing, its Endpoints object is empty, every rendering call fails after its
3-second timeout, and loads queue at the border. Both renderer pods are
Running and Ready the whole time, the submitter is Ready too, and the
namespace emits no Warning event: the case is quiet by design. The frozen
analogs are `t2-selector-drift-empty-endpoints` and the two v2 cross-namespace
cases; this is the most-repeated atom in the existing roots, so it is the
sharpest transfer test in the held-out set.

## Wait condition

`wait.sh` gates on Endpoints `customs-docs/manifest-renderer` having no
addresses **and** both renderer pods `Ready=True` **and** the submitter log
carrying the `UNREACHABLE` line. Empty Endpoints with unready pods would be a
readiness case, not this one.

## Gold-side asymmetry

- **`service/manifest-renderer`** (gold) — the selector is the only wrong
  value in the namespace. The Deployment's own selector (`app:
  manifest-renderer`) matches its pods, which is why the ReplicaSet manages
  them without complaint; the Service's does not.
- **`deployment/manifest-renderer`** — relabelling its pods to
  `manifest-render` would also fill the Endpoints, but it would require a
  change to a healthy workload *and* its selector (immutable on a
  Deployment: a recreate), to accommodate a typo in the one object that is
  wrong. The frozen contract's rule for selector cases applies unchanged:
  the Service whose selector is wrong is the object whose spec must change.
- **`deployment/manifest-submitter`** — reports the symptom; its URL names
  the right Service on the right port.

## Determinism

Single-node kind, kindnet, no NetworkPolicy; only the node-cached
`busybox:1.36`. A selector that matches no pod yields an empty Endpoints
object immediately and permanently; the submitter's `wget -T 3` fails on
every attempt (no endpoints: connection refused on the ClusterIP), so the
UNREACHABLE line appears within the first poll. The submitter runs `httpd` in
the background for its own readiness, so it stays Ready while it logs — the
page is a success-rate alert, and the baseline's dump policy (describes and
logs of not-ready rows only) has no not-ready row to curate here.

## Counterfactual verification (authoring contract rule 6)

Rehearsed live on 2026-11-08 (04:38–04:41 UTC) **before** the pristine
capture, on a separate `inject.sh --no-capture` injection:

| step | observed |
|---|---|
| fault live (wait.sh, within the first poll) | `endpoints/manifest-renderer <none>` while both renderer pods and the submitter are `1/1 Running`; `service/manifest-renderer` selector `{"app":"manifest-render"}`; submitter log `renderer http://manifest-renderer.customs-docs.svc:8080/health UNREACHABLE after 3s; filing request failed, load held at border` every 10s; **zero** Warning events in the namespace |
| applied `gold.json`'s `remediation_summary` verbatim | `kubectl patch service manifest-renderer -n customs-docs -p '{"spec":{"selector":{"app":"manifest-renderer"}}}'` → `patched` |
| recovery | within 15s (04:40:36 → 04:40:51 UTC): Endpoints `10.244.0.16 10.244.0.17` (both renderer pods), submitter log `rendered and filed batch; renderer OK` on its next poll; no pod restarted, no Deployment changed |

The cluster was then reset by `inject.sh` (namespace wipe) and re-injected
clean for the capture below.

## Capture

`evals/inject.sh --id t2-customs-filing-timeouts --root evals/scenarios-heldout`,
2026-11-08T04:41:58Z, server v1.37.0, capture schema 3: 285 files, 1.7M. The
fault manifested on the first clean injection. Verified after the capture:
`ns/customs-docs/endpoints.json` has no subsets for `manifest-renderer`,
`ns/customs-docs/services.json` carries the selector `app: manifest-render`,
`cluster/events.json` holds 0 Warning events for the namespace, and
`cluster/get-all.txt` has 0 not-ready rows in it — so the baseline's
dump for this case is the page and `get all -A` alone, by the dump policy's
own rule.
