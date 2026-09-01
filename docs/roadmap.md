# Roadmap — from replay demo to production diagnosis

Derived from a structural analysis of the shipped system: four independent
reviews mapped the agent loop, the eval harness, the fixture/capture layer,
and the design record; three ideation passes proposed extensions against
those maps; a synthesis pass deduplicated and ranked them. Every item below
is grounded in a named seam of the current code, not a wish.

## What "more complex" means for this codebase

Six structural walls define the ceiling today. Every roadmap item attacks one
of them.

1. **Single-fault by schema** — `submit_answer` takes exactly one
   `failing_resource` and the scorer requires exactly one gold class, so
   compound incidents are inexpressible.
2. **One point in time** — fixtures are a single snapshot, so flapping, slow
   leaks, and progressing-vs-stuck faults are invisible to every tool.
3. **Closed object universe** — kinds are hardcoded in twin rosters
   (`evals/capture.sh` and `solution/fixture.py`); CRDs, operators, and
   admission webhooks don't exist; `find_consumers` knows only four
   reference-edge types.
4. **No metrics channel** — pressure faults are only visible post-mortem as
   OOMKilled statuses.
5. **Context doesn't scale** — the first message embeds the whole namespace
   overview and the conversation never compacts, so a big cluster hits the
   salvage path, not a deeper search.
6. **Verification assumes a frozen snapshot** — citation re-execution is free
   and deterministic only because the "cluster" is a file tree; live state
   breaks the V1/V6 design as-is.

## The one highest-leverage next step

**Build the read-only live-cluster adapter.** A `solution/live.py` that
mirrors `solution/fixture.py`'s exact public signatures over kubectl reads,
with `validate.py`'s citation checks switched to recorded-transcript replay
against the ToolLedger (live re-execution against mutating state is
undefined). The design record already pre-vets this as bounded to a single
module (README.md, "From replay to production"). It converts the
entire verified-diagnosis apparatus from a replay demo into a tool an
on-call engineer can point at a real cluster, it is the gating prerequisite
for every product-direction item in Tier 3, and its one open design
question — what verification means on mutating state — is exactly the risk
worth retiring before investing further in lab-only benchmark depth.

## Invariants — what extensions must NOT break

- **Frozen benchmark artifacts stay byte-identical.** The `case-set-freeze`
  tag forbids touching `evals/scenarios/`, `evals/fixtures/`, and
  `evals/scoring.py`; all new cases, fault classes, and scorer changes land
  in additive v2 roots/modules with their own freeze tags.
- **The frozen-set bar stays asserted.** `evals/verify_reported.py` keeps
  asserting the declared bar in `evals/reported.json` (pooled 36/36,
  resource 36/36, confirmed-wrong ≤ 0, beats both arms, rules failing ≥ 3
  cases); new sets add their own bar blocks and never relax this one.
- **The fault-class enum never reaches the agent.** The leak-tripwire test
  stays green for every extension — including retrieved cross-incident
  memory text, prompt examples, and any new arm's instruction surface.
- **Every cited quote stays mechanically checkable** against bytes the run
  actually produced: the ToolLedger remains the evidence store of record,
  with snapshot re-execution in fixture mode and recorded-transcript replay
  (never live re-fetch) in live mode.
- **Verdicts stay discrete and mechanically earned.** No numeric confidence
  anywhere in a report, and no path to `confirmed` that bypasses the
  `validate.py` violations list.
- **Remediation keeps the human-approval gate.** The model can never invoke
  an executor; server-side dry-run precedes explicit human approval; writes
  run under per-incident RBAC scoped to the cited (kind, namespace, name).
- **Live mode is read-only by construction** — get/list-only
  ServiceAccount, no exec, no secret get — and every live read passes the
  capture-grade scrub before entering model context.
- **No plaintext secret ever enters a fixture, prompt, or report.** The
  capture scrub plus its self-check gate is mandatory on every new capture
  path, including the digest-preserving variant.
- **Failure-honest accounting is preserved.** Errored or validation-failed
  answers score wrong (never skipped or defaulted); infrastructure failures
  abort to disclosed-partial bundles; every budget-cap hit exits through the
  salvage path as a schema-valid, labelled inconclusive.
- **The rules-only ablation rides along** as a pre-registered control arm on
  every new case set (one lambda in `run_eval.py`'s ARMS registry), keeping
  the floor proof that the loop beats a decision tree.
- **Capability claims follow the pre-registration method** documented in
  `docs/experiments/`: per-case predictions, a fixed adopt/revert rule, one
  scoring invocation.

## Tier 1 — harder cases on the existing harness (days each)

Each lands as a new unfrozen case set plus small table, signature, or bar
extensions; the loop, validation gate, snapshot model, and eval harness stay
structurally as-is.

### 1.1 Degraded-but-benign red herrings in the noise pack — S

Extend the all-healthy noise pack with deterministically
unhealthy-but-irrelevant workloads (a crashlooping canary, a failing
CronJob, a perpetually Pending pod) so triage must reject plausible decoys
instead of homing on the only broken object in the cluster.

- **Changes:** `evals/scenarios/_noise/generate.sh` ROWS table additions
  (the pure-function generator stays byte-identical on rerun); new
  T3-style cases in an additive scenario root through unchanged
  `inject.sh`/`capture.sh`; prototype a distractor-citation metric offline
  from `rows.jsonl` matched_classes before touching any scorer.
- **Payoff:** removes the "any unhealthy object is the answer" giveaway and
  directly stresses the ruled-out-alternatives requirement (validate.py V7)
  with zero scorer or agent changes.

### 1.2 Authoring cluster v2: 3-node kind with Calico — L

Recreate the authoring cluster as multi-node kind with an enforcing CNI,
unlocking the scheduling/taint/topology-spread and NetworkPolicy/DNS fault
families that the current single-node, unenforced-netpol authoring contract
structurally excludes.

- **Changes:** new kind config plus a v2 authoring contract; a sanctioned
  node-prep phase (taint/cordon) in `evals/inject.sh`; CoreDNS ConfigMap
  and kube-system pod logs added to `capture.sh`'s roster; a netpol
  podSelector/namespaceSelector branch in `solution/tools.py`
  `_reference_paths`; new classes (node-taint-unscheduled,
  affinity-conflict, netpol-deny, dns-resolution-failure) in a v2 scorer
  module, red-teamed against the existing pod-unschedulable and rbac-denial
  vocabulary via the standing gold self-classification test.
- **Payoff:** converts the two highest-frequency real incident families
  (network/DNS and scheduling topology) from constitutionally excluded to
  evaluable, with no change to the snapshot replay model.

### 1.3 Admission-webhook outage cases — M

Author `failurePolicy: Fail` webhook faults where every Deployment update
fails with "failed calling webhook" in ReplicaSet events while the true
failing resource is a cluster-scoped WebhookConfiguration outside today's
visible universe.

- **Changes:** add validating/mutatingwebhookconfigurations to
  `capture.sh`'s cluster-scoped roster mirrored in `solution/fixture.py`
  CLUSTER_KINDS (the documented lockstep pair); lift `inject.sh`'s webhook
  refusal for the new set only; a webhook-admission-block signature
  red-teamed against rbac-denial's "denied" group; one namespace-
  normalization convention for cluster-scoped gold.
- **Payoff:** tests cause-outside-the-symptom-namespace reasoning on the
  classic "nothing deploys and nobody knows why" incident using only
  table-level capture changes.

### 1.4 Cross-namespace attribution plus the V2 namespace-tag fix — M

Author single-fault cases whose cause and symptom live in different
namespaces, while replacing V2's substring namespace-admissibility (unsound
once names overlap: `prod` vs `prod-eu`) with exact per-citation namespace
tags recorded on each ToolInvocation.

- **Changes:** ExternalName-target and ingress-backend branches in
  `solution/tools.py` `_reference_paths` (find_consumers inherits them);
  `validate.py` V2 rewritten to compare the namespace tags already
  recorded at the `namespaces_touched` hook — the tightening pre-specified
  in solution/README.md; new additive cases with `gold.failing_resource`
  deliberately in the non-paged namespace.
- **Payoff:** makes multi-tenant causality expressible and fixes the
  admissibility check before scale breaks it silently.

### 1.5 Secret-value faults via digest-preserving scrub — M

Swap the constant redaction scrub for a salted per-value HMAC digest so
secret equality/mismatch becomes observable without exposure, enabling
wrong-password and stale-credential-rotation cases.

- **Changes:** `capture.sh` JQ_SCRUB swaps constant redaction for salted
  HMAC with the self-check gate asserting no plaintext survives; one
  protocol note in `solution/prompts.py` teaching digest comparison
  (prompt-only); a secret-value-mismatch class red-teamed against
  bad-config-ref's "secret … not found" group.
- **Payoff:** makes an entire in-principle-undiagnosable fault family
  (credential drift after rotation — a top real pager) diagnosable while
  fixtures stay committable and shippable.

### 1.6 LLM-layer hardening with cost/latency as scored bars — S

Add bounded retry/backoff and a model-to-price table at the converse seam,
and assert per-case cost and latency ceilings in the eval bar — both are
recorded in `metrics.json` today but never scored.

- **Changes:** `common/llm.py` `converse` gains exponential backoff on
  429/5xx (billing 400s still abort via InfrastructureError per the
  disclosed-partial protocol) and a price dict replacing the single
  hardcoded row; `cost_usd_max`/`duration_s_max` keys in a v2 reported-bar
  block with matching assertions in `evals/verify_reported.py`;
  MAX_CASE_USD stays the hard stop with retry bursts counted against it.
- **Payoff:** retires the first-rate-limit-kills-a-live-incident risk and
  makes a 10x-costlier-but-correct agent visible on the bar for every
  future set.

## Tier 2 — capability jumps (a week-plus each)

Each is a change to the agent loop, capture pipeline, answer schema, or
validation gate that unlocks a scenario class the current architecture
cannot express — and each lands at a seam the subsystem maps explicitly
pre-identify.

### 2.1 Read-only live-cluster adapter with transcript-replay verification — M

The headline item above.

- **Changes:** new `solution/live.py` reusing `fixture.py`'s `_ALIASES`; a
  fixture-vs-live selector threaded through `diagnose` and
  `tools.py dispatch`; `validate.py` `_reexecute` branches on mode to
  re-read ToolLedger recorded bytes (the existing synthetic-entry fallback
  seam), with verification commands emitted as a recipe plus a fresh
  timestamped execution attached rather than asserted equal; live-read
  scrub reusing the capture scrub regexes; get/list-only ServiceAccount,
  per-call timeouts, TOOL_RESULT_CHAR_CAP retained, kubeconfig context
  pinned and stamped into `metrics.json`.
- **Payoff:** the single move from replay demo to a tool an on-call
  engineer runs, and the gating prerequisite for Tier 3.

### 2.2 Timeline fixtures and snapshot-diff tooling — L

Capture two or three per-phase snapshots per case (t0 healthy post-setup,
t1 fault manifesting, t2 evolved or partially self-healed) with a snapshot
index on fixture reads and a `snapshot_diff` tool, so change-over-time
becomes citable evidence while every snapshot stays a frozen replayable
tree.

- **Changes:** `evals/inject.sh` calls `capture.sh` once per phase (the
  setup.yaml two-phase sequencing already exists) into
  `fixture/<id>/t0..tN` with `scenario.yaml` bumped to
  schema 2 / mode "timeline" via its explicit versioning seam; a snapshot
  parameter on `fixture.py` load_kind/logs/events/describe (single layout
  authority); one `snapshot_diff` ToolSpec plus dispatch branch,
  auto-citable via READ_TOOL_NAMES/V6; an additive `validate.py` check
  requiring cross-snapshot citations for temporal verdicts; a
  `decisive_transition` gold field and a timeline heading in the report
  contract, all in a v2 scorer/case root.
- **Payoff:** unlocks flapping-readiness, slow-leak-to-OOM, and
  progressing-vs-stuck faults — the largest real-pager family a single
  point-in-time capture makes invisible — without sacrificing
  deterministic re-execution.

### 2.3 Multi-fault cascade tier with causal-chain gold — L

Author interacting-fault cases (fault A causes or masks fault B) with gold
as an ordered causal chain of multiple failing resources, breaking the
exactly-one-root-cause assumption baked into Answer, Gold, submit_answer,
and score_case.

- **Changes:** cases via `inject.sh`'s setup.yaml two-phase path (fault A
  in setup, fault B in fault.yaml) in a new scenario root; Gold/Answer v2
  with a `failing_resources` list plus causal order, landed at the
  parse_answer/load_gold choke point in a new scorer module (frozen
  `scoring.py` untouched); set-equality over multiple gold classes with
  `_DOMINATED_BY` re-audited (dominance collapse currently deletes
  legitimately co-gold symptom classes); submit_answer schema v2 with
  V3/V7 updates; a `--root` flag on `run_eval.discover_cases` plus a v2
  bar block.
- **Payoff:** makes genuinely compound incidents expressible for the first
  time and tests root-versus-induced-fault separation instead of rewarding
  shotgun answers.

### 2.4 Metrics evidence channel — M

Add a `query_metrics` tool serving resource-usage evidence — captured
kubectl-top and Prometheus dumps in fixture mode, allowlisted read-only
PromQL templates in live mode — so pressure faults are observable before
they collapse into post-mortem OOMKilled statuses.

- **Changes:** `capture.sh` entries for `kubectl top pods/nodes` and
  Prometheus range-query JSON (the in-band capture_error pattern handles
  absence); metrics-server added in the v2 authoring contract; one
  ToolSpec plus dispatch branch with TOOL_RESULT_CHAR_CAP applied; live
  mode gates allowlisted PromQL templates with max-window and max-series
  caps — no ad-hoc model-authored query strings.
- **Payoff:** restores the evidence channel real on-call diagnosis reaches
  for first and re-admits the node-pressure fault class the design record
  explicitly cut.

### 2.5 Human-approved remediation executor with dry-run and auto-verify — L

Widen remediation from a single field edit to a small typed verb set
(patch-field, rollback-revision, scale, restart) executed only through a
server-side dry-run → explicit human approval → scoped apply →
re-verification → rollback pipeline the model can never invoke.

- **Changes:** a typed remediation union on the submit_answer schema with
  `validate.py` V3 extended additively to bind each verb to the failing
  resource; new `solution/remediate.py` invoked only post-report by the
  human entry point; a per-incident RBAC role granting write on only the
  cited (kind, namespace, name); captured pre-state for rollback and
  per-action audit lines via the run_id tracing in `common/runlog.py`.
- **Payoff:** closes the diagnose-fix-verify loop for the
  rollback/scale/restart actions most real incidents actually need, while
  keeping a human approval on every consequential action.

### 2.6 Big-cluster context scaling — M

Parameterize the noise generator to 100+ namespaces, page the namespace
overview and first message, and add mid-loop compaction that digests stale
tool_result blocks — safe because quotes verify against the ledger and
fixture, not the conversation — so cluster size costs money instead of
correctness.

- **Changes:** parametric ROWS count in
  `evals/scenarios/_noise/generate.sh` as the test bed (the capture scrub
  self-check re-validated at 10x file count); offset/limit paging on
  `render_namespace_overview` and a summarized first message (not-ready
  workloads verbatim, healthy ones as counts); a compaction pass in the
  turn loop rewriting old tool_result blocks to one-line digests keyed by
  citation ids; budget constants retuned from persisted `metrics.json`
  per the documented observed-maxima method; context-size and walk-depth
  fields added at the `_metrics` emit point; a scale tier added to TIERS.
- **Payoff:** converts scale from capability-fatal (salvage-path
  inconclusive at hundreds of pods under MAX_TURNS=14) to cost-linear —
  the precondition every live, fleet, and production scenario shares.

### 2.7 Open the object universe: discovery, CRDs/operators, richer edges — L

Replace the hardcoded twin kind rosters with `kubectl api-resources`
discovery admitting CRDs, webhook configurations, and apiservices; add a
compressed cluster-scoped Role/ClusterRole projection; extend
`find_consumers`' four reference edges with ingress-backend, HPA
scaleTargetRef, netpol-selector, and ownerReference branches.

- **Changes:** `capture.sh` NS_KINDS becomes api-resources discovery with
  in-band capture_error records covering RBAC-denied kinds;
  `fixture.py` NAMESPACED_KINDS/CLUSTER_KINDS/_ALIASES extended in
  lockstep; new branches in `_reference_paths`; RBAC served as an
  overview-style projection honoring the existing per-fixture context
  guard; a purpose-built operator plus CRD case family (cr-spec-invalid,
  operator-reconcile-stalled) proving the opened universe end to end.
- **Payoff:** makes operator- and admission-managed infrastructure — a
  dominant real-incident class currently invisible by construction —
  discoverable, attributable, and evaluable.

### 2.8 Machine-visible differential and adversarially-earned "confirmed" — L

Add a `log_hypothesis` ledger tool binding every ruled-out claim to
verifying citation ids, require a counterfactual discriminator (a
verification command whose expected output would differ under the top
alternative), and gate `confirmed` behind a fresh-context skeptic pass that
can raise a named violation.

- **Changes:** `log_hypothesis` ToolSpec plus dispatch branch (the
  ToolLedger's synthetic-entry fallback already handles non-read tools); a
  `discriminates_against` field on submit_answer with a new additive
  `validate.py` check re-executed per the V6 pattern; new
  `solution/skeptic.py` reusing `common/llm.py` `converse`, hooked into
  the acceptance branch before "Accepted", with a MAX_SKEPTIC_ROUNDS
  constant beside the five budgets and its cost folded into the metrics;
  a prompt protocol directing one probe batch per open hypothesis per
  turn.
- **Payoff:** converts `confirmed` from checklist-earned to
  exclusivity-earned, retiring the confirmed-wrong risk that grows exactly
  where harder multi-decoy cases can satisfy the current mechanical bar
  with circumstantial evidence.

## Tier 3 — product direction (behind the live adapter)

Each presupposes the live adapter or a mature harder-case corpus and points
at the on-call product this becomes; staged directional bets, not
next-sprint work.

### 3.1 Alert-to-report incident entry point — M

A thin webhook service converts Alertmanager/PagerDuty payloads into
`page.txt`, runs `diagnose()` on the live adapter, and posts the scrubbed
report — verdict, evidence chain, verification recipe — to the incident
channel, with remediation surfaced only as explicit human-approval actions.

- **Changes:** a new service module mapping alert payload to the page text
  the loop consumes; a programmatic entry beside the CLI; report output
  passed through the capture-grade scrub before leaving the machine;
  per-alert dedup with MAX_CASE_USD as the per-incident spend guard and
  report-only default.
- **Payoff:** moves the trigger from an eval-harness file path to the page
  itself and lands the report where the responder already is — the
  difference between a benchmark and an on-call tool.

### 3.2 Multi-cluster fleet mode with cluster-tagged citations — L

Tag every tool call and citation with its cluster, add a `fleet_overview`
projection rendering per-cluster health the way `namespace_overview`
renders namespaces, and let one diagnosis span kubeconfig contexts behind
an explicit per-incident cluster allowlist.

- **Changes:** a context registry in `solution/live.py`; a cluster field on
  ToolInvocation/ToolLedger records flowing into answer citations
  (building on the exact-tag rework from Tier 1); `fixture/<cluster>/`
  subtrees via the single-layout-authority seam for offline fleet cases;
  per-cluster read-only contexts and per-cluster budget scaling so a
  fleet walk cannot silently multiply MAX_TOOL_CALLS.
- **Payoff:** matches the tool's scope to real on-call scopes and makes
  right-object-wrong-cluster confusion — inexpressible today —
  representable and testable.

### 3.3 Game-day and postmortem replay corpus — M

Point the existing inject/capture pipeline at a multi-node staging cluster
during chaos game-days and reconstruct postmortem faults into new frozen
case sets, so difficulty grows from real incidents instead of hand-authored
fault atoms.

- **Changes:** `capture.sh` parameterized on kubeconfig context (with the
  api-resources discovery from 2.7); `fault.yaml` optionally templated
  from postmortem writeups (fault-as-code is mechanically validatable end
  to end via `inject.sh`); `run_eval.discover_cases` already globs new
  scenario roots with zero harness change; the mandatory scrub self-check
  before commit, a staging-only kubeconfig gate, and a per-set freeze tag
  preserving the gold-first counterfactual-rehearsal contract.
- **Payoff:** creates the data flywheel where every real incident becomes a
  reproducible benchmark case — the long-term moat and the only
  sustainable source of genuinely hard cases.

### 3.4 Cross-incident memory as verified retrieval priors — M

Index accepted reports and retrieve top-k similar past incidents at case
start as hypothesis priors the agent must still verify through tools —
real on-call diagnosis is dominated by recurrence.

- **Changes:** a new `solution/memory.py` (keyword or embedding index over
  bundle report.md/answer.json files); an optional prior-incidents
  parameter on the first message; memory-off default for the frozen
  12-case arm, with "solution-memory" registered as a separate arm in
  `run_eval.py`'s ARMS registry; a mechanical check that retrieved text
  never contains fault-class enum values, keeping the anti-leak tripwire
  green.
- **Payoff:** cuts turns on repeat fault shapes — the dominant real-world
  case once bounded depth meets bigger clusters — without contaminating
  the benchmark or weakening verify-through-tools discipline.
