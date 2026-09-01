# Scoring spec — frozen before the first scored run

This is the disclosed definition of "good", written down before the first
scored run. It binds both arms: `baseline/` and `solution/` are scored by the same
code on the same cases. The prose here explains the rubric;
[`evals/scoring.py`](scoring.py) is **normative** — where they could disagree,
the code wins, and both freeze together.

**Change policy (design req 5):** the rubric and case set may be tightened
before the freeze, each change CHANGELOG'd with the evidence that motivated it.
The freeze point is the annotated tag **`case-set-freeze`** at this
repository's initial commit: the frozen paths — `evals/scenarios/`,
`evals/fixtures/`, `evals/scoring.py` — change only via disclosed
decision-doc updates, checkable with
`git log case-set-freeze..HEAD -- evals/scenarios evals/fixtures evals/scoring.py`.
The operative reproducibility mechanism is `evals/reported.json` + `make verify`,
which re-derives every cell from the pinned bundles and asserts the solution
arm's declared bar; the rules/baseline cells are re-derived and printed for
comparison, not independently asserted.

## Answer schema (the scored extract)

Every arm must emit, per case, one JSON object (parsed by
`scoring.parse_answer`; validation errors fail the case — nothing defaults):

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "1-3 sentences: the causal mechanism only, no differentials.",
  "verdict": "confirmed | probable | inconclusive",
  "missing_evidence": "required iff verdict is inconclusive: what would settle it"
}
```

- `failing_resource` names the resource **whose spec must change** (the owning
  deployment, not the crashing pod). Kinds are normalized through kubectl-style
  aliases (`deploy`/`deployments` → `deployment`); after normalization the
  match is exact on (kind, namespace, name). No partial credit: naming the
  symptom pod when gold names the deployment is wrong.
- `mechanism` is freeform prose. The fault-class enum is **never shown to the
  agent** (anti-multiple-choice); the scorer maps prose → class as below.
- The full report (evidence chain, ledger, verification recipe) is a separate
  markdown artifact per the report contract in
  `docs/decisions/problem-selection.md`; `report_contract_violations` checks it
  mechanically (four required section headings, numeric self-confidence banned).

## Ground truth

Each case ships `evals/scenarios/<id>/gold.json` (validated by
`scoring.load_gold`): case_id, tier (T1/T2/T3), failing_resource, fault_class,
mechanism_summary, decisive_evidence, remediation_summary. A standing test
requires every gold's own `mechanism_summary` to classify to exactly its
`fault_class` — the rubric must at minimum recognize its own ground truth.

## Mechanism → class rubric (mechanical)

The fault-class enum is the 12-atom roster from the decision doc. Each class
has a **signature**: groups of case-insensitive regex alternatives; a
mechanism matches a class iff every group matches at least once. Exact
patterns: `_SIGNATURES` in `scoring.py`. In concept:

| Class | Must express (one term per group) |
|---|---|
| service-selector-mismatch | selector + labels/match/endpoints |
| rbac-denial | rbac/role/rolebinding/serviceaccount/permission + forbidden/denied/cannot/unauthorized/403 |
| resource-quota-exceeded | quota + exceeded/exhausted/blocked/rejected/limit |
| unbound-pvc | pvc/persistentvolumeclaim + unbound/pending/storageclass/provisioning |
| bad-config-ref | configmap/secret + not found/renamed/couldn't find/invalid key |
| init-container-failure | init container + fail/error/exit/crash/stuck |
| image-pull-backoff | image/tag/registry + pull/not found/invalid/manifest unknown |
| pod-unschedulable | scheduling/no node + insufficient/requests/allocatable/capacity |
| readiness-probe-failing | readiness + fail/unready/not ready/connection refused/wrong port |
| oom-killed | oom/out of memory/memory limit/137 + killed/exceeded/limit |
| app-crashloop | crashloop/restart/backoff + exit/fatal/panic/at startup |
| rollout-stuck | rollout/rolling update/replicaset/progress deadline + stuck/paused/deadlock/maxUnavailable |

No alternative appears in both of a class's groups — a single token ("OOM",
"crashloop", "137") must never satisfy a class alone, or an answer that merely
mentions a decoy workload (even to rule it out) multi-matches and scores
wrong. Tightened pre-freeze from the roster red-team (2026-08-29), per the
change policy above; regression tests in `tests/test_scoring.py` pin the
natural phrasings for both directions.

**Classification rule:** compute the set of fully-matching classes, then apply
the disclosed **dominance collapse** (root-over-symptom: when a specific
mechanism co-matches with the symptom bucket it produces, the bucket drops —
e.g. an OOM answer that also says "restarts/backoff" collapses to `oom-killed`;
`app-crashloop`, `pod-unschedulable`, `rollout-stuck` are the dominated
buckets, exact pairs in `_DOMINATED_BY`). The mechanism is **class-correct iff
the surviving set is exactly the gold class** — zero matches (undercall) and
ambiguous multi-matches (shotgun answers) both score wrong, deliberately.

## Metrics

- **Primary: root-cause identification rate** = fraction of cases with
  `resource_correct AND class_correct`. Reported overall **and per tier**
  (T1/T2/T3), baseline vs solution vs change.
- **Secondary: calibration (selective accuracy)** = the same rate conditioned
  on the report's discrete verdict, across seeds. `confirmed`-but-wrong is
  counted separately (`Summary.confirmed_wrong`) and is the heavily penalized
  cell; the headline trust claim is "when the agent said confirmed, it was
  right N/N times". Numeric self-confidence anywhere in a report is a contract
  violation, not a metric.
- Secondary reporting per the decision doc: remediation correctness and cost
  per case are harness-recorded and disclosed alongside, not scored by this
  module. **Human time per task is not reported at all** — no human trial was
  run, so any figure would be invented; README states this explicitly rather
  than reporting a guess.

## Anti-leak invariants (machine-enforced where possible)

1. **The enum never reaches the agent.** No file under `baseline/` or
   `solution/` may contain the class slugs, the name `FaultClass`, or an
   import of `evals.scoring` (pytest tripwire:
   `test_fault_enum_never_leaks_into_agent_dirs`). The semantic rule is
   stronger — prompts must not enumerate candidate fault types in any
   spelling — and is held by review; the tripwire catches the mechanical
   forms.
2. **The agent sees only cluster state.** The fixture-backed tool layer serves
   paths under the fixture's `cluster/` and `ns/` subtrees plus the paged
   symptom (`page.txt`) as task input. `scenario.yaml` (capture ledger),
   `evals/scenarios/<id>/` (fault.yaml, notes.md, gold.json) are never
   tool-visible.
3. **Gold presence is gated**: every fixture must have its
   `evals/scenarios/<id>/gold.json` (checkpoints.sh fixture gate) and every
   gold must validate + self-classify (pytest).

## Reproducibility protocol (design req 7)

Scored runs use the pinned model ID `claude-opus-5` with no sampling
parameters — the model removed `temperature`/`top_p`/`top_k` (a request
carrying them is rejected), so "temperature 0" is unavailable by construction
and run-to-run stability is reported over ≥3 replicate runs instead. Raw
transcripts and per-case score rows (`CaseScore` fields, including
`matched_classes` for audit) land in `evals/results/` so every number in
README/CHANGELOG verifies without a rerun. (Wording updated pre-freeze per the
change policy above, when the harness was built against the live API.)
