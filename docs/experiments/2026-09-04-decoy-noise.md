# Experiment — degraded-but-benign decoys in the noise pack (roadmap 1.1)

## What was being tested

**Claim under test** (roadmap 1.1, verbatim): extending the all-healthy noise
pack with deterministically unhealthy-but-irrelevant workloads "removes the
'any unhealthy object is the answer' giveaway and directly stresses the
ruled-out-alternatives requirement (validate.py V7)".

That claim assumes the arms find the answer *by noticing what is broken*. The
experiment tests whether they do.

## Pre-registration

The prediction was committed to `STATUS.md` before the noise pack existed, at
the close of the 2026-09-02 session:

> If the solution arm still scores 100% with decoys present, that is itself the
> result — escalate to roadmap 1.3 (webhook configurations are outside the
> captured object universe entirely, so headroom there is near-certain).

Fixed in advance: the adopt/revert rule is that a score unchanged by the decoys
kills 1.1 as a source of headroom rather than motivating more decoys.

## Method — a controlled A/B, not a new case

`t3-crossns-decoys` is `t2-crossns-externalname-selector` with **byte-identical
Kubernetes objects** (verified by comparing both `fault.yaml` files with comment
lines stripped) and one difference: the v2 noise pack. That pack copies the
frozen 20 healthy namespaces **verbatim** from `evals/scenarios/_noise/` and
appends three decoys, so the healthy half cannot contribute to any difference
either:

| decoy | namespace | shape it imitates |
|---|---|---|
| `canary-runner` | `release-canary` | app-crashloop (unset env, CrashLoopBackOff) |
| `nightly-export` | `report-exports` | a failing CronJob (missing mount, Failed jobs) |
| `model-trainer` | `batch-compute` | pod-unschedulable (`cpu: "512"`, Pending) |

All three are conditional per authoring contract rule 1 — each was corrected
live and observed to recover before the pristine capture. Any score difference
between the two cases is therefore attributable to the decoys and nothing else.

## Result — the claim is false for both LLM arms

3 replicate runs per arm per case.

| arm | no decoys | with decoys | resource_correct (with decoys) |
|---|---|---|---|
| rules | 0/1 | 0/1 | 0/1 |
| baseline | 1/3 | 0/3 | **3/3** |
| solution | 3/3 | **3/3** | **3/3** |

Neither LLM arm was misled, for two entirely different reasons:

- **The solution never saw the decoys.** Across all three runs it touched
  exactly two namespaces, `storefront` and `payments` — no decoy namespace, no
  healthy-noise namespace. It navigates by following references from the page,
  never by surveying the cluster for unhealthy objects, so an unrelated broken
  workload is not something it can be distracted by. Its `ruled_out` lists name
  alternatives along the causal path (gateway pods down, wrong alias target, a
  NetworkPolicy, a broken pod spec) and never a decoy.
- **The baseline saw them and was unmoved.** The decoys appear in its curated
  dump 46 times (`model-trainer` 28, `nightly-export` 13, `canary-runner` 5),
  and it still named `service/payments-gateway` in `payments` in all three runs.

The baseline's 1/3 → 0/3 movement is **not** a capability drop: `resource_correct`
is 3/3 in both conditions. The whole difference is `class_correct`, the mechanism
sentence failing the rubric's vocabulary — the same artifact recorded in
CHANGELOG [13] and docs/failure-modes.md (2026-09-04), measured here a second
time.

## What the decoys did cost

The solution's accuracy was unchanged, but its work was not:

| | no decoys | with decoys | change |
|---|---|---|---|
| turns | 5.0 | 6.7 | +34% |
| tool calls | 6.3 | 10.0 | **+59%** |
| cost / case | $0.2032 | $0.2421 | +19% |
| ruled-out items | 3.0 | 4.3 | +43% |

The extra calls were spent going deeper inside `storefront` and `payments`, not
investigating decoys. A bigger cluster made the agent more thorough on the path
it was already following — cost scaling with cluster size, which is the concern
roadmap 2.6 owns, not a difficulty signal.

## Decision (per the pre-registered rule)

**1.1 is closed as a source of headroom, and the noise pack is kept.** The
giveaway it was designed to remove was not what either arm relied on: one does
not survey, and the other surveys and is not fooled. Escalate to roadmap 1.3 as
pre-registered — webhook configurations are absent from the captured object
universe entirely, so the ceiling there is structural rather than
presentational, and structural is what "no headroom" requires.

The pack itself stays in `evals/scenarios-v2/_noise/`: it costs nothing, it is a
more honest cluster than an all-healthy one, and it is now the measured control
for any future claim that a case is hard *because* the cluster is noisy.

## Cost

$1.30 for the four scored bundles in this experiment (solution and baseline,
3 runs each, on the decoy case); the no-decoy comparison bundles were already
committed from 2026-09-04's earlier run.
