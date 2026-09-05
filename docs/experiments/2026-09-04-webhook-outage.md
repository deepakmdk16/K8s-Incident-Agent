# Experiment — the first case whose cause is outside the captured object universe (roadmap 1.3)

## What is being tested

**Claim under test** (roadmap 1.3 and CHANGELOG [14]'s pre-registered
escalation): a case whose cause is a cluster-scoped admission webhook
configuration creates *structural* headroom above the saturated frozen set,
because the object whose spec must change is absent from what either LLM arm
can read today — not merely far away, hidden in noise, or in another
namespace, which are the presentational difficulties that [13] and [14]
showed do not bite.

Two earlier attempts failed for the same reason: the solution follows
references from the page and is not distracted by what it never reads; the
baseline is handed a curated whole-cluster dump and distance within that dump
costs it nothing. This case removes the object from both arms' reach:

- the baseline's frozen dump policy (page + `get all -A` + describes of
  not-ready rows + last-50 log lines) contains no cluster-scoped object;
- the solution's `get_object` refuses every cluster-scoped kind by design
  (solution/README.md disclosure 5), and before this slice the kind was not
  even captured.

The fixture itself now carries the object (capture schema 2), so the case is
scorable and the ceiling is the arms', not the benchmark's.

## The case

`evals/scenarios-v2/t2-checkout-release-stalled/`: a release of
`checkout/checkout-api` (three replicas, maxSurge 0 / maxUnavailable 1) stalls
at 0 updated and 2 ready because an orphaned `ValidatingWebhookConfiguration`
named `workload-standards` — its `clientConfig.service` names a Service in a
namespace that no longer exists, `failurePolicy: Fail` — makes the API server
refuse every pod create with `failed calling webhook ... service "policy-guard"
not found`. Gold is that configuration (cluster-scoped, class
`webhook-admission-block`, scored by `evals/scoring_v2.py`). Nothing in
namespace `checkout` is wrong, and rolling back does not recover (the old
ReplicaSet would then have to create a pod, and is refused too — rehearsed live,
see the case's `notes.md`).

The case id is symptom-named on purpose. The frozen ids encode their mechanism
and reach the agent in the first message; this case's whole point is that the
cause is invisible, so the id hands nothing over.

## Pre-registration — written before any scored run

Fixed in advance, per arm, 3 replicate runs for the LLM arms:

| arm | prediction | reasoning |
|---|---|---|
| rules | 0/1; names `deployment/checkout-api` | no analyzer keys on admission; the rollout analyzer fires on `Progressing=False` and names the Deployment — the designed distractor |
| baseline | pooled **0/3**; `resource_correct` 0/3; `class_correct` likely 3/3 | its dump carries the webhook error text (describes of the 2/3 Deployment and the 0-ready ReplicaSet), so it will describe the mechanism in the rubric's words, but it cannot see the configuration and will name the Deployment, the ReplicaSet, or invent a configuration name |
| solution (unchanged) | pooled **0/3**; `resource_correct` 0/3; verdicts `inconclusive` or `probable`; **confirmed-wrong 0** | `get_events` shows it the same error text; `get_object` cannot serve the configuration; V7 cannot be satisfied for an object it never read, so a `confirmed` verdict is unearnable and the gate should force the honest verdict |

**What the class sub-score can and cannot tell us.** The webhook error text
reaches both LLM arms (the ReplicaSet describe in the baseline's dump;
`get_events` for the solution; the API server's own log lines say the webhook
is "failing closed"), and with rollout-stuck dominated by the new class,
`class_correct` is expected to be near-unconditional for any answer that quotes
the event. This case's signal is therefore `resource_correct`, and it is
reported beside every pooled number. The Deployment's `Available` condition
reads `True` at capture (two of three old pods still serve), so a rules
analyzer keyed on it does not fire; the one keyed on `Progressing=False` does.

**Two ways to name the object without reading it, pre-registered as leaks.**
The configuration's name (`workload-standards`) shares no token with the
webhook name, the Service or its namespace, so it cannot be derived from the
event text and appears nowhere under the fixture's namespaced tree (verified
after capture with `grep -rl workload-standards evals/fixtures/<id>/ns/`).
Three validator paths could still surface it, none of which involves reading
the object: (a) V4 EXISTS lists the names present when a guessed name misses;
(b) V7 accepts a "defect" citation whose cited *call arguments* name the object
even when that call returned an ERROR (`get_object` on a cluster-scoped kind
returns "cluster-scoped and not served here"); (c) `describe` on the
configuration returns the deterministic string "no describe captured for
validatingwebhookconfiguration/<name> in namespace <ns>", which re-verifies
under V1, names the object for V7's quote clause, and reads PRESENT as a V6
verification check. These are tool-limitation errors, not cluster state — unlike
"no configmap named X", which is legitimate evidence of a missing reference and
must stay citable. All three are checked in every solution transcript
(`rejections.json`, `transcript.json`); a `resource_correct` row reached through
any of them is reported as a validator leak, not as capability, and closing them
(V4 not listing names for kinds no read tool serves; V6/V7 not counting
tool-limitation errors as evidence) is the first task of the next slice.

**Decision rule, fixed now.** If the solution scores 0/3 with confirmed-wrong 0,
the headroom is real and structural, and the next slice serves the two webhook
kinds through `get_object` (a cluster-scoped read tagged namespace `""`, hence
V2-admissible by construction) plus a webhook → Service reference edge, and
re-scores; the pre-registered prediction for that slice is 3/3. If the solution
names the configuration **without reading it** — guessing the name from the
webhook's name in the event message and letting V4 EXISTS confirm the guess —
that is a leak path in the validator to close *before* the next slice, and the
score does not count as capability. If confirmed-wrong is above 0 on either
arm, that row is examined per CLAUDE.md before anything else is concluded: a
pooled gap names a difference, never its cause.

**No v2 bar block** is added to `evals/reported.json` on this evidence either
way: a bar asserting a ceiling both arms hit would be a claim about the
benchmark, not about an arm.

## Method

One scoring invocation per arm on the captured fixture, through
`evals/run_eval.py --scenarios-root evals/scenarios-v2 --cases
t2-checkout-release-stalled`, scored by `evals/scoring_v2.py` (the frozen
rubric re-keyed by value plus one class; parity pinned by
`tests/test_scoring_v2.py`). Results are read from `rows.jsonl` sub-scores
(`resource_correct`, `class_correct`, `matched_classes`, verdicts) before the
pooled number is quoted.

## Result — 2026-09-05

Bundles: [rules](../../evals/results/20260905T044845Z-rules/summary.md),
[baseline](../../evals/results/20260905T044850Z-baseline/summary.md),
[solution](../../evals/results/20260905T044853Z-solution/summary.md). Every
number below is re-derivable from their `rows.jsonl`.

| arm | pooled | resource_correct | class_correct | verdicts | confirmed-wrong |
|---|---|---|---|---|---|
| rules | 0/1 | 0/1 | 0/1 (`rollout-stuck`) | confirmed | 1 |
| baseline | **0/3** | 0/3 | 3/3 | confirmed x3 | **3** |
| solution (unchanged) | 1/3 | **3/3** | 1/3 | inconclusive x2 (salvaged), probable x1 | **0** |

**Rules** — as predicted: the rollout analyzer fired on `Progressing=False`
and named `deployment/checkout-api` with a confident verdict.

**Baseline** — as predicted, in every detail. All three runs identified the
kind (`ValidatingWebhookConfiguration`), described the mechanism in the
rubric's words, and could not name the object: they wrote `policy-guard`, the
Service name the event message prints, in one run adding "confirm exact object
name via kubectl get validatingwebhookconfigurations" *inside the name field*.
Three confident verdicts on a guessed name.

**Solution** — the pre-registered leak fired, and the pre-registered gate held.
The pooled 1/3 is not the finding; the rows are:

- *`resource_correct` 3/3 is the V4 leak, not capability.* In all three runs
  the name `workload-standards` first appears in a **rejection message** —
  `V4 EXISTS: no ValidatingWebhookConfiguration named 'policy-guard' in
  namespace 'cluster-scoped'. Present: workload-standards` — after the agent
  guessed `policy-guard` exactly as the baseline did. No tool result in any run
  contains the name: `get_object` on the kind was refused in run 1 (turn 3) and
  on the named object in run 2 (turn 7); `describe` in run 3 (turn 6) returned
  "no describe captured". Zero successful reads of the object across 25 tool
  calls. Per the decision rule these rows count as a validator leak.
- *The verdict gate did its job.* Every run proposed `confirmed` at least once
  and was refused with the unmet condition named — "one verified item with role
  'defect' whose citation is about workload-standards; one PRESENT verification
  check that names workload-standards" — and none could supply it, because no
  tool can produce output about that object. Runs 1 and 2 hit the reject cap and
  exited through the salvage path as labelled `inconclusive`; run 3 submitted
  `probable`. **Confirmed-wrong 0, against the baseline's 3**, on the same
  unreadable object. That is the calibration claim this arm exists to make,
  measured on the first case where it costs something.
- *`class_correct` 1/3 is a salvage artifact.* The salvage path's mechanism
  sentence is fixed text ("did not close … could not be verified"), which
  classifies to nothing by design; run 3's own sentence classified to exactly
  `webhook-admission-block`. Nothing here is about vocabulary.
- *Cost.* $0.61 per case (8–9 turns, 8–9 tool calls, 4 submit attempts every
  run) against $0.18 on the frozen set: the loop spent its budget failing to
  read one object and arguing with the validator about it.

Spend: $0.40 baseline + $1.83 solution = **$2.23** for this experiment.

## Decision (per the pre-registered rule)

The headroom is real and structural: no arm read the object, and the one arm
that names it did so through a validator message. Two slices follow, in order:

1. **Close the leaks in the validator first** (no capability change): V4 must
   not list the names present for a kind that no read tool serves; V6/V7 must
   not accept a tool-limitation error ("cluster-scoped and not served here",
   "no describe captured for …") as evidence or as a PRESENT check, while a
   cluster-state error ("no configmap named X") stays citable. Re-score; the
   prediction is `resource_correct` 0/3, confirmed-wrong 0.
2. **Then raise the ceiling**: serve the two webhook kinds through `get_object`
   and `describe` (cluster-scoped reads tag namespace `""`, hence V2-admissible)
   plus a webhook → Service reference edge, and re-score. Prediction: 3/3, all
   `confirmed`, turns and cost back near the frozen-set means.

No v2 bar block is added on this evidence, as pre-registered.
