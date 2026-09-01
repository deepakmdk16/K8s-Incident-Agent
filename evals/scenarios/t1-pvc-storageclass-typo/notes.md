# t1-pvc-storageclass-typo — scenario notes

**Tier:** T1 (textbook single fault). **Status:** authored 2026-08-29 (Sat
morning); captured and scored in the frozen case set (fixtures:
`evals/fixtures/t1-pvc-storageclass-typo/`).

**Namespaces owned:** `analytics` (rule 4).

**Provenance:** authored by the operator from roster row #5 and the
2026-08-29 red-team constraints (immutable volumeClaimTemplates); applied
only via `evals/inject.sh` — run date recorded at capture below.

**Ground truth (informal — the scored version is `gold.json`):**

- Failing resource: `analytics/statefulset/metrics-db`
- Fault class: unbound PVC — nonexistent storageClass
- Mechanism: the volumeClaimTemplate names `storageClassName: fast-ssd`; no
  such class exists (cluster has only `standard`), so `data-metrics-db-0`
  stays Pending unbound and pod `metrics-db-0` waits on its volume forever.
- Decisive evidence: the PVC's `not found` event naming `fast-ssd`, the
  Pending PVC phase, the Pending pod, and `storageclasses` listing only
  `standard`.
- Remediation: **recreate, don't edit** — volumeClaimTemplates are
  immutable, and a recreated StatefulSet reuses an existing PVC by name, so
  the stale Pending PVC must be deleted too (gold spells out all three
  steps). An "edit the template" remediation would be rejected by the
  apiserver (red-team finding, encoded here).

**Wait condition:** PVC Pending + `fast-ssd ... not found` event on the PVC
+ pod Pending (wait.sh; 5s poll, 300s cap).

**Why deterministic:** a reference to a nonexistent storageClass can only
ever produce an unbound Pending claim — no provisioner races (none exists
for `fast-ssd`), no image pulls (pod never starts), no host dependence; the
PVC controller re-emits the not-found event periodically so capture timing
cannot miss it.

**Gold-side asymmetry:** none needed — creating a `fast-ssd` StorageClass is
theoretically possible but not defensible on this cluster (nothing else
references it; `standard` is the provisioned default); the StatefulSet's own
template is the spec that must change.

**Counterfactual-verification record (rule 6): 2026-08-29 ~07:25 IST** —
`inject.sh --no-capture` manifested all three gates (wait.sh: PVC Pending;
event `storageclass.storage.k8s.io "fast-ssd" not found`; pod Pending).
Applied gold's three-step remediation: deleted the StatefulSet, deleted the
stale Pending PVC, re-applied with `storageClassName: standard` → PVC
`data-metrics-db-0` **Bound** (1Gi, standard) and pod `metrics-db-0` **1/1
Running** within 3s — confirming both the fix and the stale-PVC step gold
calls out (a recreated StatefulSet reuses the existing claim by name).
Wiped and re-injected cleanly for the pristine capture.
