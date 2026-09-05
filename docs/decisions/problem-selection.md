# Problem lock: Kubernetes incident diagnosis

**Chosen: Kubernetes incident-diagnosis agent** with a recorded-cluster-state
evaluation. The kill conditions below defined when the problem would have been
abandoned or hardened mid-build; none fired.

## Why this problem

An on-call engineer paged about a broken workload runs a slow, expertise-gated
hypothesis→evidence→verify loop; the root cause routinely hides far from the
symptom across dozens of resources that all report healthy. That loop is the
compressible share of MTTR, and automating it demands a *genuine* investigation
agent — the decisive evidence must be found, connected, and verified, not
pattern-matched.

The choice was pressure-tested before locking: three adversarial review lenses
(reproducibility auditor, skeptical reviewer, execution-risk analyst) attacked
the plan; their surviving objections became the binding design requirements
below. This decision record is the panel's documented outcome.

## Eval design (committed before any scored run)

- Ground truth by **fault injection**: scenarios are code (manifests +
  wait-condition), executed against kind clusters in Docker; full cluster state
  snapshotted to text fixtures. **The eval replays offline from fixtures** —
  reproducing it needs no cluster; live kind is demo-only.
- **Primary metric:** root-cause identification rate. Agent answers as
  (failing resource + freeform mechanism); a disclosed rubric maps mechanism →
  fault class for mechanical scoring. **The fault-class enum never appears in
  the agent prompt** (anti-multiple-choice).
- ≥10 cases target; ≥1 hard case (overlapping faults / red-herring symptom).
  Same cases, both sides.
- Secondary reporting: remediation correctness, human time per task, cost per
  task — baseline / solution / change.
- Baseline = documented "what a rushed human would paste" curation: one prompt
  with `get all -A` + describes of not-Ready resources + last-N log lines;
  token counts stated per case; resource difference vs agent disclosed.

## Case design: difficulty comes from composition

The fault-class roster below is the set of deterministic *atoms*; case
difficulty comes from how scenarios *compose* them — never from exotic fault
machinery. Two structural rules:

1. **Symptom-first paging.** Every scenario ships the page the on-call
   receives (e.g. "checkout p99 latency SLO burn", "deploy stuck >15min") and
   the agent starts from that symptom — never from "find what's broken in
   this cluster". Diagnosis quality is judged against the page it answers.
2. **Three tiers, metric reported per tier** (overall + per-tier in the final
   table):
   - **T1 — textbook single fault** (4-5 cases): baseline is *expected* to do
     well here. These prove the comparison is fair and calibrate the scorer.
   - **T2 — cascade / distant cause** (4-5 cases): symptom far from cause.
     Candidates: Service selector typo presenting as frontend timeouts;
     NetworkPolicy in another namespace blocking app→DB; HPA blocked by
     ResourceQuota presenting as latency; bad ConfigMap value picked up only
     by the one restarted pod (intermittent 1-of-N errors); failing readiness
     probe → empty Endpoints → upstream 503s; PodDisruptionBudget deadlocking
     a rollout; per-pod `dnsConfig` misconfig (external calls fail,
     cluster-internal fine).
   - **T3 — adversarial** (2-3 cases, incl. the required hard case):
     overlapping faults; a noisy red-herring CrashLoop in an unrelated
     namespace while the paged service fails for a quieter reason; 150+
     resources / 20+ namespaces of realistic noise.

All tiers are built from the same atoms and stay deterministic-by-construction
in kind. T2/T3 are where the one-prompt baseline and the rules-only ablation
arm are expected to degrade — that expected degradation IS the measured-
improvement narrative, stated before running (define "good" first).

## Report contract & confidence calibration

The deliverable is a report the on-call engineer can act on **without redoing
the investigation**. Four required sections, all auditable rather than
asserted:

1. **Evidence chain** — every claim cites the exact tool output it came from.
2. **Investigation ledger** — differentials considered and ruled out, each
   with its ruling-out evidence. Proves the agent didn't anchor on the first
   plausible cause.
3. **Discrete verdict, mechanical criteria — never a percentage.**
   `confirmed` requires direct causal evidence linking mechanism to symptom;
   `probable` = consistent but indirect evidence; `inconclusive` must name
   the additional evidence that would settle it. Numeric self-confidence is
   banned in the report (uncalibratable decoration; AI-draft smell).
4. **Verification recipe** — the 2-3 commands a human runs to independently
   confirm in <2 minutes.

**Secondary metric — calibration (selective accuracy):** accuracy conditioned
on verdict level, across seeds. `confirmed`-but-wrong is the heavily
penalized cell; the headline trust claim is "when the agent said confirmed,
it was right N/N times" — which is precisely the condition under which the
human-time-saved claim is honest.

**Difficulty escalates across iterations; the scoreboard stays fair.** Cases
are built in tier order (T1 → T2 → T3) as solution iterations progress; every
addition is a disclosed CHANGELOG experiment (adding a T2 case because the
agent aces T1 is legitimate experiment design). The complete tiered set
freezes at a tagged commit before the final scored run; the reported table
comes from ONE invocation of that frozen set on both sides. Hardening cases
after seeing final results is the violation; hardening before the freeze,
disclosed, is the method.

## Design requirements (from the red-team; each is binding)

1. **Scored artifact is a standalone API-driven harness** — plain scripted LLM
   loop + fixture-backed kubectl-shaped tools; the reproduction path is
   `git clone && make eval` + one API key env var. Claude Code is the coding
   agent that *builds* the harness, never the runtime.
2. **Secrets scrubbed by construction**: capture script drops/redacts Secret
   resources, tokens, kubeconfig; `checkpoints.sh` secret grep extended to
   `evals/fixtures/` in the same commit that creates the directory.
3. **Deterministic fault roster, fixed now** (node-pressure eviction and
   CoreDNS surgery are stretch goals, cut without ceremony): service selector
   mismatch · RBAC denial · ResourceQuota exceeded · unbound PVC (bad
   storageClass) · bad configmap/secret ref · init-container failure ·
   ImagePullBackOff (bogus tag) · unschedulable (requests > allocatable) ·
   failing readiness probe · OOMKilled · CrashLoopBackOff · bad rollout.
4. **Over-capture from the first commit** (all describes, logs incl.
   `--previous`, events as JSON, manifests, node/PVC/endpoints state) +
   fixture-completeness check in checkpoints.sh. Scenarios that resist live
   reproduction in a 90-min timebox are hand-authored from a healthy snapshot
   and labeled `authored` vs `captured` in the scenario manifest.
5. **Freeze the scoring spec before the first scored run**; case set frozen at
   a tagged commit; the reported table comes from ONE harness invocation
   running both sides; any case-set change is CHANGELOG'd with evidence.
6. **Score the baseline early, on the first cases** — "baseline green" means
   *scored*, not runs. If it aces them, harden by scale
   (150+ resources, 20+ namespaces, noise + red herrings), not exotic faults —
   big noisy clusters simultaneously fix realism, telegraphed-answer optics,
   and headroom, and they are the honest reason targeted tool queries win.
7. **Reproducible headline number**: temperature 0, pinned model ID, metric
   reported over ≥3 seeds, raw transcripts + per-case scoring checked into
   `evals/results/` so the claim verifies without a rerun.
8. **Pre-empt the "a decision tree does this" objection**: rules-only diagnoser
   ships as a second comparison arm (ablation) in the changelog; ≥3 cases
   where rules demonstrably fail; k8sgpt cited in README as prior art the eval
   is built to beat.
9. **Architecture freeze before the final scored run**: single agent + scoped
   tools + verify-before-assert loop. Anything later must pass one test:
   "moves a number more than an hour of hardening would?".

## Addendum 2026-09-04 — a 13th fault class, in an additive scorer only

**Condition that fired:** the frozen 12-case set is saturated (solution 36/36,
CHANGELOG [8]) and two pre-registered attempts to create headroom without new
fault machinery failed informatively ([13], [14]): difficulty that is only
presentational does not bite. The next case class has to make the cause
structurally absent from what an arm can read, and the deterministic roster
above (design requirement 3) has no such atom.

**What changes:** one class, `webhook-admission-block` (an admission webhook
the API server cannot call, `failurePolicy: Fail`, refusing the create it
intercepts; the failing resource is the cluster-scoped webhook configuration),
defined in `evals/scoring_v2.py` and scored only for cases under
`evals/scenarios-v2/`. The frozen roster, the frozen rubric (`evals/scoring.py`),
the frozen 12-case set and the primary metric are untouched; the v2 scorer
re-keys the frozen tables by value and a parity test requires identical results
on every frozen phrasing. A cluster-scoped gold writes `"namespace": ""` and the
v2 scorer treats every "no namespace" spelling as equal for a cluster-scoped
kind. The v2 enum is under the same never-in-agent-code invariant as the frozen
one (`tests/test_scoring_v2.py`).

**Method:** per-arm predictions and the adopt/next rule are written before the
scored run in `docs/experiments/2026-09-04-webhook-outage.md`. No v2 bar block
is added to `evals/reported.json` on this case's evidence.

## Kill conditions (→ abandon or harden, per item — none fired)

1. First checkpoint: snapshot harness + **≥2 recorded scenarios** + scored
   one-prompt baseline not demonstrably green end-to-end. (Bar of 2 declared
   in advance — the full 10+ cases are only needed later; do not panic-abort
   a viable design.)
2. More than ~5 of 12 fault classes cannot be made deterministic within their
   timebox (mitigated by roster choice + authored-fixture path above).
3. Baseline already scores ≥80% on the case set and a half-day of
   scale-hardening cannot open a measurable gap.
