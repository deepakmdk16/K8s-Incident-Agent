# t2-quota-blocks-scale — scenario notes

**Tier:** T2 (symptom far from cause: latency page on a workload, fault in a
policy object). **Status:** authored 2026-08-29; captured and scored in the
frozen case set (fixtures: `evals/fixtures/t2-quota-blocks-scale/`; the
inject run and counterfactual rehearsal are recorded below).

**Namespaces owned:** `checkout` (rule 4).

**Provenance:** authored from roster row #8 and the 2026-08-29 red-team
constraints (quota-first document order, admitted-count variance). inject.sh
run: 2026-08-29 — recorded with the counterfactual rehearsal below.

**Ground truth (informal — the scored version is `gold.json`):**

- Failing resource: `checkout/resourcequota/checkout-quota`
- Fault class: quota rejection of pod creates
- Mechanism: `checkout-quota` hard-caps `pods: "2"`; the checkout-api
  deployment asks for 6 replicas. Quota admission rejects the ReplicaSet's
  pod creates beyond the cap, so the deployment never reaches its replica
  count and per-pod load stays spike-high (the paged latency burn).
- Decisive evidence: ReplicaSet `FailedCreate` events whose message contains
  `exceeded quota: checkout-quota` — reason alone is not decisive (a missing
  ServiceAccount also yields FailedCreate); the message text is.
- Remediation: raise (or remove) the `pods` cap on `checkout-quota`; no
  deployment change.

**Wait condition:** `wait.sh` polls (5s interval, 300s cap) for a ReplicaSet
FailedCreate event in `checkout` containing `exceeded quota: checkout-quota`;
exits 0 on manifest, 1 with a diagnostic dump on timeout.

**Why deterministic:**

- Document order is load-bearing: the ResourceQuota is the first document
  after the Namespace, so it exists before the Deployment does — the quota is
  armed (or arming) before the ReplicaSet controller's first pod create.
- The 2-vs-6 margin covers the quota-admission arming window: the ReplicaSet
  controller creates pods in slow-start batches (1, then 2, then 4), so even
  if the window admits an extra pod or two, later creates land after arming
  and are rejected; the controller retries forever, so `FailedCreate
  ... exceeded quota` events recur rather than appearing once.
- **Admitted-pod count varies** with that window (typically 2, occasionally
  more). It is deliberately asserted nowhere — not in wait.sh, not in
  gold.json, and drawing no conclusions from it here.
- No image pulls: only node-cached `busybox:1.36`. No host-dependent values;
  a pods-count quota needs no resource requests. Single-node kind suffices.
- Rule 1 counterfactual: the container script is healthy by construction —
  every admitted pod runs its ConfigMap-mounted serve loop (rule 2: logic one
  hop from the spec) indefinitely. The fault lives only in the quota object,
  so correcting it recovers the deployment with no other change.

**Gold-side asymmetry:** two objects could defensibly change — lower the
deployment's replicas back, or raise the quota. The page pins the intent: the
scale-up to 6 was a deliberate response to a live traffic spike, so the stale
capacity policy is the object whose spec must change → gold =
`resourcequota/checkout-quota`.

**Counterfactual-verification record (rule 6): 2026-08-29 ~02:15 IST** —
`inject.sh --no-capture` manifested the fault (wait.sh: `FailedCreate:
Error creating: pods ... is forbidden: exceeded quota: checkout-quota,
requested: pods=1, used: pods=2, limited: pods=2` — the arming window
admitted exactly the cap on this run). Applied gold's remediation
(`kubectl -n checkout patch resourcequota checkout-quota --type=merge -p
'{"spec":{"hard":{"pods":"8"}}}'`); recovery observed: `deployment
"checkout-api" successfully rolled out`, `available=6/6` with no deployment
change. Wiped and re-injected cleanly for the pristine capture. Adversarial
verification substituted by operator review (the verifier agent run was cut
short by an API usage limit — disclosed).
