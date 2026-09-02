# Improvement Changelog

> One entry per meaningful iteration, written when it happens. Every entry
> connects to the evidence that guided the next decision. Template below —
> copy it for each entry.

<!--
## [N] <short title> — <date, time UTC>

**Change:** what changed in the solution (or agent design).
**Why:** the observation/evidence that motivated it (link: eval run, trace file,
failing test, measurement).
**Evidence after:** measurement showing the effect (link to eval output / test /
trace). Numbers over adjectives.
**Next decision it drove:** what this told us to do next.
-->

## [12] The first case whose cause is in another namespace — 2026-09-02, 03:46 UTC

**Change:** roadmap 1.4's authoring half.
`evals/scenarios-v2/t2-crossns-externalname-selector/` — a T2 case where the
page fires on `storefront` and the object whose spec must change is
`service/payments-gateway` in **`payments`**. `storefront/checkout-api` gates
its readiness on a payment gateway it reaches through an ExternalName alias;
the Service that alias points at selects `app=payments-gateway` while its pods
carry `app=payments-gateway-api`, so its Endpoints never populate. New cases
land in an additive root — `evals/run_eval.discover_cases` takes a `root`, and
`--scenarios-root` selects it — so the frozen 12-case set keeps its identity
and its count. Fixtures stay in the single `evals/fixtures/` tree, where the
credential scan and schema gate already reach them.

**Why:** every case in the frozen set has its cause and symptom in one
namespace, so none of them can tell an agent that follows a reference across a
boundary from one that searches the paged namespace exhaustively. It is also
the case that exercises the V2 admissibility rule ([10]) in the direction that
matters.

**Evidence after:** three findings, two of them defects the case exposed in
code that has been shipping since the start.

1. *The V2 fix does what it was built for.* Against the captured fixture, the
   page alone licenses `['', 'storefront']`; after the agent reads the
   ExternalName Service and quotes its target, `payments` becomes citable.
   That works **only** because [10] treats `.` as a label separator, so
   `payments-gateway.payments.svc.cluster.local` names `payments` — the
   design decision now has a case that would fail without it.
2. *An ExternalName Service rendered as a broken one.* The namespace overview
   printed `service/payments-gateway selector={} endpointAddresses=0` — an
   alias has no selector and no Endpoints **by design**, so it read as
   identical to a Service whose selector matches nothing, inviting a diagnosis
   of the healthy alias instead of the fault it points at. Now rendered as
   `type=ExternalName externalName=…`. No frozen fixture contains an
   ExternalName Service, so no scored row can move.
3. *A Service another namespace depends on reported no consumers.*
   `find_consumers` on the payments Service said "no workload in payments
   references service/payments-gateway" — true and useless. It now reports the
   aliasing Service and the namespace it lives in.

The free rules arm scores the case **0/1**, and its log names the reason:
`2 analyzer(s) fired in ns storefront, chose readiness` — it answered
`deployment/checkout-api` in the *paged* namespace, the designed distractor,
against gold `service/payments-gateway` in `payments`
([evals/results/20260902T034542Z-rules/](evals/results/20260902T034542Z-rules/summary.md)).
The case discriminates exactly as intended, and it proves the harness runs the
additive root end to end for $0.00. Counterfactual rehearsed live before the
pristine capture (authoring contract rule 6): gold's remediation applied
verbatim moved Endpoints from `<none>` to two pod IPs and checkout-api from
0/2 to 2/2 Ready, with nothing in `storefront` touched — recorded in the
case's `notes.md`. Suite 238 → **248 passed**, `bash scripts/checkpoints.sh`
**0 failure(s)**, fixture schema complete at 13.

**Next decision it drove:** the case is authored, captured and free-scored, but
no LLM arm has seen it — the baseline and solution numbers on it need a scored
run, which costs API spend and is the next decision to take. The roadmap's
ingress-backend edge was deliberately **not** built: no case exercises it, and
untested speculative code in the tool layer is worse than a recorded gap.

## [11] Cost and latency were recorded but never scored — 2026-09-02, 00:22 UTC

**Change:** the declared bar now asserts spend and wall-clock, not just
correctness. [evals/verify_reported.py](evals/verify_reported.py) re-derives
mean cost and mean duration per case from the `metrics.json` committed beside
every `answer.json`, prints both for all three arms, and fails the gate if the
solution arm exceeds the two new ceilings in
[evals/reported.json](evals/reported.json). The bar logic was extracted into a
pure `check_bar()` so it could be tested at all — it had no tests before this,
which meant no check in it had ever been shown to fire.

**Why:** roadmap 1.6. Every scored run already wrote `cost_usd` and
`duration_s` per case and nothing read them, so an arm that reached 36/36 by
spending ten times as much would have passed the gate silently — and with the
frozen set saturated at 36/36, cost and latency are two of the few axes left
where a future change can still be measured at all.

**Evidence after:** the ceilings are set at **2x the observed pooled mean**
($0.1807/case, 44.1s/case → $0.36 and 88s), derived from the committed bundle
rather than picked: the same case varies up to **2.42x** in cost across
replicate runs, so a per-case ceiling would fire on noise, while the 12-case
run means moved only $0.157/$0.181/$0.204 (±15%) across the three runs. A
per-case *cost max* would also have been toothless, since `MAX_CASE_USD`
already truncates it at $0.60. Seven new tests in
[tests/test_verify_reported.py](tests/test_verify_reported.py) — suite 231 →
**238 passed** — trip each ceiling and each previously-untested check;
`bash scripts/checkpoints.sh` reports **0 failure(s)** and the gate line now
reads `36/36 … $0.1807 and 44.1s per case`. The measured comparison this
exposes: the solution buys six pooled points over the baseline at **1.55x**
the spend ($0.1807 vs $0.1163) and effectively the same wall-clock (44.1s vs
43.0s), and mean × 36 reconciles to each bundle's `summary.json` totals to the
cent ($6.51, $4.19).

**Next decision it drove:** the three remaining roadmap items in flight (1.1,
1.4, 2.1) all need an authoring cluster, and each will add rows to a bar that
now costs something to miss — so the next slice is bringing up the authoring
cluster, not more offline harness work.

## [10] The admissibility check matched namespaces by substring — 2026-09-01, 14:11 UTC

**Change:** V2 admissibility now decides whether a text *names* a namespace by
exact DNS-1123 label tokens instead of substring containment. One helper,
[solution/fixture.py](solution/fixture.py) `namespaces_named_in`, placed beside
`namespaces()` because that module is already the authority on what a namespace
is; [solution/validate.py](solution/validate.py) calls it at both sites that
grew the admissible set (the page-text seed and each verified quote), and
[solution/agent.py](solution/agent.py) `paged_namespace` calls it in the
fallback that picks the paged namespace in the first place.

**Why:** no eval row failed — the defect is latent, which is why it was worth
finding before harder cases make it fire. The rule was
`{ns for ns in known if ns in page_text}`: pure substring containment, so a
page naming `prod-eu` silently admitted citations from `prod`, and the same
test one layer up in `paged_namespace` resolved the paged namespace itself to
`kube-system` from the words `kube-system-canary` (reproduced directly against
the `t2-rbac-sync-forbidden` fixture: old rule → `kube-system`, new rule → no
match). A wrong paged namespace poisons the admissible set from the first
citation, so fixing V2 alone would have left the hole one layer up. Today's
12 fixtures have no overlapping namespace names, which is the only reason it
never fired; roadmap item 1.4 authors cases whose cause and symptom sit in
different namespaces, and it would have fired there.

**Evidence after:** three regression tests that fail on the old rule and pass
on the new one — two in
[tests/test_solution_validate.py](tests/test_solution_validate.py), one per
admissibility site, and one in
[tests/test_solution_agent.py](tests/test_solution_agent.py) for the
paged-namespace fallback. Suite goes 228 → **231 passed**, `bash
scripts/checkpoints.sh` reports **0 failure(s)** (ruff clean, pyright strict
0 errors), and `make verify` re-derives the unchanged **36/36** solution row
from the committed bundles: the tightening only narrows admissibility, and it
narrows nothing the frozen evidence relies on.

**Next decision it drove:** the exactness precondition for roadmap 1.4 is now
in place, so the remaining half of that item — authoring cross-namespace cases
with `gold.failing_resource` deliberately outside the paged namespace, plus
the ExternalName and ingress-backend branches in `_reference_paths` — is
unblocked and needs an authoring cluster, not more validator work.

## [9] The "a decision tree does this" objection, measured: rules reach 27/36 — 2026-08-29

**Change:** a third comparison arm, `ablation/` — a deterministic non-LLM
diagnoser: 12 k8sgpt-style analyzers over the same fixtures, emitting the same
report contract, scored by the same frozen scorer. No model, no network, $0.00,
0.4 s for the full 12x3 matrix, byte-identical across runs.

**Why:** design requirement 8. "Couldn't a decision tree do this?" is the
likeliest objection to a Kubernetes-diagnosis agent, and the only honest
answer is a number. Building the arm *after* seeing all 12 cases invites two
opposite frauds — overfitting it into a lookup table, or crippling it into a
strawman — so the method was committed as a pre-registration BEFORE the arm
existed ([docs/experiments/2026-08-29-rules-ablation.md](docs/experiments/2026-08-29-rules-ablation.md)):
generic signatures only with k8sgpt as the reference bar, no case identifiers in
`ablation/`, every ambiguity resolved in the arm's favour (it is handed the paged
namespace and allowed to name the object whose spec must change), and no tuning
after the score is seen. The pre-registered prediction was a deliberately **high**
9-12 of 12, with the stated expectation that a good score would still overstate
the engine.

**Evidence after:**
[evals/results/20260829T101751Z-rules/](evals/results/20260829T101751Z-rules/summary.md)
— **27/36 pooled**, 9/12 in each of the three runs. Bottom edge of the predicted
range; both cases flagged `uncertain` failed, and one predicted `correct` failed.

| metric | rules | baseline | solution |
|---|---|---|---|
| root-cause identification | 27/36 | 30/36 | **36/36** |
| T1 | 12/15 | 15/15 | **15/15** |
| T2 | 9/15 | 10/15 | **15/15** |
| T3 | 6/6 | 5/6 | **6/6** |
| resource identification | 33/36 | 33/36 | **36/36** |
| right object, sentence unmatched | 3 | 3 | **0** |
| confirmed-wrong | 3 | 3 | **0** |

The three cases it fails in every run break it three different ways, and none is
a missing analyzer:

1. **`t1-pvc-storageclass-typo` — cause/symptom inversion.** Right object,
   wrong thing said about it: reported the pod as unschedulable when it is
   unschedulable *because* its PVC never bound. Both observations are true; only
   one is the cause, and precedence order cannot prefer a cause because it does
   not know which observation explains which.
2. **`t2-rbac-sync-forbidden` — the broken-reference blind spot.** Returned
   **zero findings**. The analyzer reads the 403 from the log, reads the pod's
   ServiceAccount, and looks for the RoleBinding naming it — but the injected
   fault is that the binding names `inventory-synk` against a real
   `inventory-sync`. The edge it traverses is the edge the fault removed. *A
   rules engine that navigates by reference cannot diagnose a broken reference*:
   it would have to hypothesise about what is **missing**, not match what is
   present. The solution arm gets this case in all three runs.
3. **`t2-quota-blocks-scale` — right object, sentence unmatched.** Named the
   correct ResourceQuota; its canned sentence matched no class because the
   scorer's signature wants a standalone `quota` and the sentence only had it
   inside `ResourceQuota`. **Disclosed as a wording artifact, not a reasoning
   gap**, left uncorrected under the no-tuning rule and reported in its own row.
   Granting it, rules would tie the baseline at 30/36 — still 6 behind solution.

**The finding the score does not show:** 5 of 12 cases fired more than one
analyzer, and in every one the losers were dropped by precedence alone, on no
evidence. That includes `t3-overlapping-config-and-oom`, which the arm gets
**right** — three analyzers fired, and config-ref outranks OOM only because the
pre-registration put it there. Flip that pair, as an equally defensible
severity-first runbook would, and it confidently returns the OOM'd report worker:
real, loud, and not what the page is about. The 6/6 on T3 is a coin that landed
right twice, and the bundle records the coin. The calibration column says the
same thing in the arm's own voice: it asserted `confirmed` and was wrong, because
it has no notion of doubt to report.

**Next decision this drove:** the gap is now bounded from below by something
better than "a simpler thing scores worse" — it is bounded by *which* 3 cases and
*why*. `evals/reported.json` pins the bundle and `make verify` re-derives all
three columns offline; the gate now **fails if the ablation ever stops failing on
at least 3 cases**, so design requirement 8 cannot rot into a stale claim.
`tests/test_rules_ablation.py` pins the three failing cases as regression tests
and enforces the method (no case identifiers in string literals, no model or
scorer import, determinism, zero cost).

## [8] 36/36 — the reverted rules, re-scoped and pre-registered, land perfectly — 2026-08-29

**Change:** three edits to the system prompt, committed as a pre-registration
BEFORE the run ([docs/experiments/2026-08-29-mechanism-nouns.md](docs/experiments/2026-08-29-mechanism-nouns.md))
with per-case predictions for all 12 cases, a named collision watch-list, and a
fixed adopt/revert rule:
1. **Lift the over-broad prohibition.** The shipped prompt said "STOP at the
   failure ... do not carry the sentence into what restarted". For an object
   caught in a restart loop, that behaviour is the failing object's OWN
   behaviour, not a downstream effect of a different object — the clause was
   suppressing a true, cited, on-target fact. A bug fix, defensible with no
   reference to any scorer.
2. **Name Kubernetes objects by kind and name, scoped to object INSTANCES.** The
   scoping is the load-bearing detail: the unscoped version of this rule in [7]
   produced "carries no configmap read permission" and cost a t2-rbac row.
   Permission and class-of-thing phrasings ("get and list on configmaps") are
   explicitly left alone. `validate.py` V5b already demanded this for the failing
   resource; this applies it consistently to the rest of the chain.
3. **Quote observed errors and statuses rather than paraphrasing them.**
   Protective: the three winning readiness rows quote `"connection refused"`, and
   [7]'s rules paraphrased it away and lost all three.

**Why:** [7] measured the unscoped version at 33/36 -> 31/36 and it was reverted.
The per-row trace showed the losses were not the rules being wrong but the rules
being *unscoped* — and separately identified edit 1's clause as an outright bug.
**Evidence after:**
[evals/results/20260829T090941Z-solution/](evals/results/20260829T090941Z-solution/summary.md)
— **36/36 pooled**, $6.51, ~26 min.

| tier | baseline | solution |
|---|---|---|
| T1 | 15/15 | **15/15** |
| T2 | 10/15 | **15/15** |
| T3 | 5/6 | **6/6** |
| pooled | 30/36 | **36/36** |
| resource identification | 33/36 | **36/36** |
| right object, sentence unmatched | 3 | **0** |
| confirmed-wrong | 3 | **0** |

Every run 12/12. Every one of the 36 rows matched exactly one fault class. The
calibration target from [5] — drive confirmed-wrong to 0 — is met: **when the
agent said `confirmed`, it was right 36 times out of 36.**

Every per-case prediction in the pre-registration was correct; the pooled
prediction (35/36) was one row pessimistic. All four watch-list collisions were
avoided. The stated P(36/36) was 0.20 and the outcome was that branch on the
first and only attempt — the estimate was wrong because it treated the prior
losses as random variance when they had a single removable cause. That is
recorded in the pre-registration rather than quietly dropped.

**Anti-leak, held under pressure:** the first draft of edit 3 used
`CrashLoopBackOff` as an example of "a status in the cluster's own words".
`tests/test_solution_prompts.py` failed it — that string names a candidate
failure type. The prompt was changed, not the test. The same reasoning was then
applied to a phrase the tripwire cannot see: edit 1's first draft enumerated
"restarted, backed off, or retried", which would have handed over a class
signature's group-1 token by the side door. Both removed; the shipped wording
lifts the prohibition without naming any token the classifier looks for.

**Next decision it drove:** the arm is frozen at 36/36 and
[evals/reported.json](evals/reported.json) now pins this bundle, so
`make verify` re-derives 36/36 vs 30/36 from committed evidence on every commit
and fails if anything regresses. Remaining work is the rules-only ablation arm
(design req 8).

## [7] Experiment REVERTED: tuning mechanism wording trades rows, it does not win them — 2026-08-29

**Change:** two extra mechanism-writing rules were added to the system prompt
("call every Kubernetes object by its API kind" and "run the sentence through to
the state the page reports, and end there"), measured on the full frozen set,
and then **reverted**. The shipped prompt is byte-identical to the one that
produced the [6] bundle.
**Why:** [6] left T1 at 12/15 with all three losses showing
`resource_correct=True` and `matched_classes=[]` — right object, sentence the
rubric matched to nothing. A 4-case targeted probe scored 4/4 on exactly the
contested cases ($0.76), which looked like confirmation.
**Evidence after:**
[evals/results/20260829T073436Z-solution/](evals/results/20260829T073436Z-solution/summary.md)
— **31/36, down from 33/36**, confirmed-wrong 3 -> 5, $6.35.

| tier | [6] bundle | this experiment |
|---|---|---|
| T1 | 12/15 | **15/15** |
| T2 | **15/15** | 11/15 |
| T3 | **6/6** | 5/6 |
| pooled | **33/36** | 31/36 |

It fixed precisely what it aimed at — `t1-pvc-storageclass-typo` 1/3 -> 3/3 and
`t1-crashloop-missing-env` 2/3 -> 3/3, T1 back to 15/15 — and broke three cases
it was not aiming at: `t2-readiness-wrong-port` **3/3 -> 0/3**,
`t2-rbac-sync-forbidden` 3/3 -> 2/3, `t3-overlapping-config-and-oom` 3/3 -> 2/3.

**Why, exactly** (traced through the frozen classifier, per row — the first
characterisation of this in [7] was wrong and is corrected here). Each class
signature is a CONJUNCTION of two groups: an *object/noun* group and a
*symptom/error-word* group, and a class matches only if BOTH hit. The two rules
moved one dial — from **quoting observed output** toward **describing canonical
structure** — and that dial has opposite effects depending on which half of the
conjunction a given case was short of:

- **It supplied missing object nouns (the wins).** `t1-pvc` needed a
  PersistentVolumeClaim noun; "the claim generated from that template" gave the
  rubric nothing, "the PersistentVolumeClaim data-metrics-db-0" gave it the
  group-1 hit.
- **It removed symptom words (3 of the 5 losses).** All three `t2-readiness`
  rows kept group 1 (`readiness`) and lost group 2 entirely. The 33/36 sentences
  quoted the observed error — `"connection refused"` — or said "the probe
  fails"; the new ones paraphrased it into structure: "refused at the TCP
  level", "hold Ready=False", "no pod ever passing readiness". Group 2's
  alternatives are `\bfail | \bunready\b | not ready | connection refused |
  wrong port`. Synonyms, zero matches.
- **"Stay on the failing object" forbade naming the object the class is keyed
  on (1 loss).** `t3-overlapping`'s gold class needs `config ?map\b`. The 33/36
  sentence named the second object — "**ConfigMap** orders/orders-config contains
  only the key ..." — and matched. The new one, obeying the rule, wrote only
  `configMapKeyRef.key`, and `config ?map\b` does **not** match
  "configMapKeyRef" (the `\b` needs a non-word char after "map"; "K" is a word
  char). Group 1 empty, 0 classes. The irony is exact: this fault *is* a
  cross-object reference failure, and it cannot be described from inside one
  object.
- **The API-kind rule added a noun that collided with another class (1 loss).**
  `t2-rbac` run 2 wrote "carries no **configmap** read permission" — the
  canonical singular, as instructed. `config ?map\b` matches "configmap " (space
  after) where it does *not* match the earlier phrasing "get,list on configmaps"
  (the trailing "s" blocks the boundary). Plus "does not exist" for group 2, and
  the row matched **two** classes.

So the failure is not "under-naming versus over-reaching" as first written. It
is one dial with opposite signs per case, and a word-boundary away from
invisible.

**The invariant across both bundles: `resource_correct` is 36/36 in each.** Every
one of the 5 losses here, and all 3 there, named the correct object. No wording
rule moved that number, in either direction, on any run.

**Next decision it drove:** stop tuning sentence wording ad hoc against the
rubric — it is a measurement instrument with a vocabulary, and the same dial
supplies a missing noun on one case while stripping a matched symptom word on
another, so pushing it wins rows and loses rows at the same time. Re-scope
instead of re-word: pin instance names (`Kind ns/name`), quote the cluster's
own error text rather than paraphrasing it, and pre-register a per-case
prediction with a fixed adopt/revert rule before the next run — the approach
[8] takes, reaching 36/36. [6] stood as the reported result at this point in
the narrative — **33/36 vs the baseline's 30/36, T2 10/15 -> 15/15, T3 5/6 ->
6/6, with the three target rows from [5] all won** — superseded by [8]. Both
bundles stay committed so the reversal is auditable rather than asserted, and
the general finding is written up in
[docs/failure-modes.md](docs/failure-modes.md).

## [6] Solution arm scored: 33/36, T2 and T3 perfect, and every remaining loss is one failure mode — 2026-08-29

**Change:** the advanced arm ships and is scored on the same frozen 12-case set,
from the same recorded fixtures, by the same frozen scorer. It is a single agent
that walks the object graph backwards from the page over eight fixture-backed
kubectl-shaped tools, and can finish only through `submit_answer` — a validating
tool that RE-EXECUTES every citation against the snapshot, runs the 2-3
verification commands the report promises, and derives the verdict from those
outcomes instead of letting the model choose it.
**Why:** CHANGELOG [5] showed the baseline's 6 lost rows were two distinct
problems — evidence it structurally cannot reach (its curation only describes
NOT-Ready resources, so a Running-and-Ready worker's 403 log is never in the
prompt), and attribution (naming the workload that looks unhappy instead of the
object whose spec must change).
**Evidence after:**
[evals/results/20260829T064705Z-solution/](evals/results/20260829T064705Z-solution/summary.md)
— **33/36 pooled vs the baseline's 30/36**, at $6.20 vs $4.185
(~$0.172/case-run vs $0.116; 1.33M in / 136k out tokens).

| tier | baseline | solution |
|---|---|---|
| T1 | 15/15 | **12/15** |
| T2 | 10/15 | **15/15** |
| T3 | 5/6 | **6/6** |
| pooled | 30/36 | **33/36** |
| confirmed-wrong | 3 | 3 |

Row deltas: `t2-rbac-sync-forbidden` **0/3 -> 3/3**, `t2-init-wait-for-migrations`
**1/3 -> 3/3**, `t3-quiet-selector-loud-crashloop` **2/3 -> 3/3**;
`t1-pvc-storageclass-typo` 3/3 -> 1/3 and `t1-crashloop-missing-env` 3/3 -> 2/3.
The three target rows named in [5] as the design brief were all won.

**The number that matters most is not in the table: `resource_correct` is
36/36.** The agent named the correct object — the one whose spec a human must
edit — in every single run, including all three it scored wrong. All three
losses have `matched_classes=[]`: the diagnosis was right and the sentence used
vocabulary the frozen mechanism->class rubric does not recognise ("the claim
generated from that template" rather than naming the PersistentVolumeClaim;
stopping at "the container exits 1" without naming the restart behaviour the
page reports). One failure mode, not three.

Three gate defects found and fixed by the first live runs, before this matrix
(all three regression-tested): tool-call ids the model could not see and so
invented; an admissibility rule that rejected citations used to RULE OUT a red
herring, punishing the exact discipline the arm exists to enforce; and a
vocabulary ban that banned the failing resource's own name. On the case that
exposed them, the very first submission had already been correct.

**Next decision it drove:** the T1 regression is not a reasoning regression and
must not be treated as one. The fix is a mechanism-writing rule that is
independently good practice and names no failure type — name Kubernetes objects
by their API kind, and end the sentence at the cluster-observable state the page
reports — then re-run the full matrix as one invocation. If T1 does not recover,
33/36 with 36/36 resource accuracy is the honest headline and the vocabulary
sensitivity of any mechanism->class rubric becomes the headline finding.

## [5] Baseline anchored on the full 12-case set: 30/36, and the gap is structural — 2026-08-29

**Change:** first clean scored run of the complete case set — 12 cases × 3
replicate runs, pinned `claude-opus-5`, zero infrastructure failures (the
new abort guard armed but never fired).
**Why:** any improvement claim needs the baseline anchored on the same
frozen-candidate set the solution arm will run, before that arm exists
(design req 6: baseline scored first).
**Evidence after:**
[evals/results/20260829T045650Z-baseline/](evals/results/20260829T045650Z-baseline/summary.md)
— **30/36 pooled (T1 15/15, T2 10/15, T3 5/6)**, stable 10/12 on every run;
calibration: `confirmed` right 30/33, **confirmed-wrong 3**; $4.185 total
(~$0.116/case-run), 237k in / 120k out tokens. Per-case: every failure is
concentrated where the tiers were designed to hurt — `t2-rbac-sync-forbidden`
**0/3** (the sync worker is Running and Ready, so the baseline's
describes-of-not-Ready curation never sees its 403 logs or the RBAC objects
— a structural blind spot, not a sampling miss), `t2-init-wait-for-migrations`
1/3 with 2 confirmed-wrong (the roster PREDICTED this case baseline-solvable;
the prediction was measurably wrong — the init evidence is in the dump and
the one-shot still misses it), `t3-quiet-selector-loud-crashloop` 2/3 with
the red herring taking one run. Kill condition 3 checked and NOT fired:
baseline ≥80% but the gap clause fails — a 6-row + calibration gap exists
with no further hardening. Two earlier poisoned bundles from this date
remain committed as disclosed-partials (billing failures, not model data).
**Next decision it drove:** the solution arm's design brief is now written
by the data: (a) investigate Ready-but-broken resources — policy/RBAC
objects and healthy-looking workloads' logs — that dump curation
structurally omits; (b) verify-before-assert to drive confirmed-wrong 3 → 0
(the calibration headline); (c) target rows: t2-rbac ×3, t2-init ×2,
t3-quiet ×1. If the solution clears ~34/36, the comparison stands; if it
lands closer, scale-hardening T1/T2 with the noise pack (disclosed,
pre-freeze) is the honest widening lever.

## [4] Second fixture captured; declared bar met; remediation now rehearsed, not asserted — 2026-08-29

**Change:** first scenario produced end-to-end through the sanctioned path:
`inject.sh --no-capture` → fault manifests (wait.sh gates on the exact
containerd `not found` event text, fail-fast on registry throttling) → gold's
remediation applied live → recovery observed (`deployment "storefront"
successfully rolled out`, 2/2 Running) → wipe → pristine re-inject → capture.
The rehearsal record lives in the scenario's notes.md (contract rule 6).
**Why:** the roster red-team showed three cases whose remediation could be
wrong invisibly; rule 6 makes "remediation_summary" an observed fact per case
before its fixture freezes. Registry choice validated live: registry.k8s.io
emitted containerd's `not found` for the unknown repo, as designed.
**Evidence after:** `evals/fixtures/t1-imagepull-bogus-tag/` (268 files);
checkpoints "fixture schema complete (2 fixture(s))", 0 failures. The bar
declared in advance — snapshot harness + ≥2 recorded scenarios + a scored
baseline — is now met.
**Next decision it drove:** capture the remaining T1 cases the same way as
verification completes, then T2/T3; score the baseline on the widened T1 set
before building the solution arm.

## [3] Case roster designed, red-teamed; scorer tightened pre-freeze — 2026-08-29

**Change:** committed the 12-case tiered scenario roster + authoring contract
([evals/scenarios/README.md](evals/scenarios/README.md)) and the sanctioned
fault→fixture path (`evals/inject.sh`: default-ns guard, namespace wipe, noise
pack for T3, optional two-phase setup, wait-condition gate, capture). Scoring
spec tightened under the disclosed pre-freeze change policy: no signature
alternative may satisfy both of a class's groups (`crashloop`/`oom`/`137`
single-token dual-matches removed), readiness signature restricted to failure
evidence, `rolebinding`/`403` added to rbac-denial, renamed-key phrasings added
to bad-config-ref, and the shared answer contract now confines `mechanism` to
the paged symptom's causal chain (ruled-out alternatives go in the ledger).
**Why:** a 3-lens adversarial review of the roster
([evals/out/20260829-roster-red-team.json](evals/out/20260829-roster-red-team.json),
30 findings) *executed* the scorer against natural correct answers and showed
honest T3 diagnoses scoring wrong: any answer that even negated the designed-in
decoy ("the crashlooping batch job is unrelated") multi-matched via
single-token signatures, and the natural renamed-ConfigMap-key phrasing
zero-matched. It also caught determinism traps (Docker Hub rate limits can
freeze a fixture whose events contradict gold, quota admission arming race,
host-dependent CPU requests, immutable volumeClaimTemplates) now encoded as
roster rules.
**Evidence after:** `uv run pytest -q` green — 6 new regression phrasings in
`tests/test_scoring.py::test_roster_phrasings_classify_exactly` (each fails
against the pre-tightening signatures) plus the existing gold self-classify
suite.
**Next decision it drove:** author the 11 remaining scenario directories
against the hardened contract, then capture in tier order — the 2nd captured
fixture completes the declared bar. The t1 exemplar gets a disclosed re-capture
(conditional entrypoint + missing wait.sh) before the case-set freeze.

## [2] Baseline scored: 3/3 on the first case, contract-clean — 2026-08-28

**Change:** built the one-prompt baseline arm — documented "rushed human"
dump-curation policy ([baseline/README.md](baseline/README.md)), pinned
`claude-opus-5`, shared output contract (`common/report_contract.py`) so both
arms answer under identical instructions — and the scored-run harness
(`evals/run_eval.py`: arm × cases × 3 replicate runs, frozen scorer, committed
evidence bundle).
**Why:** design req 6 ("baseline green" means *scored*, not runs) and the
bar declared in advance in
[docs/decisions/problem-selection.md](docs/decisions/problem-selection.md).
**Evidence after:**
[evals/results/20260828T185739Z-baseline/](evals/results/20260828T185739Z-baseline/summary.md)
— root-cause identification **3/3** (T1), verdict `confirmed` 3/3,
confirmed-wrong 0, zero report-contract violations; 16,137 in / 12,350 out
tokens, **$0.3894 total** (≈$0.13, ≈55 s per case). Disclosed pre-freeze spec
adjustment in the same slice: the pinned model rejects sampling parameters, so
determinism is reported over replicate runs
([evals/scoring.md](evals/scoring.md)).
**Next decision it drove:** (a) baseline aces T1 as predicted — the
measured-improvement narrative now depends on T2/T3 cases where one-shot
curation misses distant causes; scenario production is next (kill condition 3
watched: if baseline ≥80 % on the full set, harden by scale, not exotic
faults). (b) The baseline's own report flagged that the injected fault's
container command is an *unconditional* stub — future scenarios get realistic
entrypoints so remediation-correctness claims stay honest.

## [1] Scoring spec frozen before any scored run — 2026-08-28

**Change:** committed the disclosed definition of "good" (`evals/scoring.md` +
normative `evals/scoring.py`): answer schema, the 12-class mechanism→class
rubric with dominance collapse, exact-resource matching, per-tier primary
metric, verdict-calibration secondary metric, and the anti-leak invariant (the
fault-class enum never reaches the agent — enforced by a pytest tripwire).
First ground truth: `evals/scenarios/t1-crashloop-missing-env/gold.json`;
checkpoints now fail any fixture lacking its gold.
**Why:** design req 5 requires "good" defined before the first run and freezes
the scoring spec ahead of any scored result — scoring decided after seeing
outputs would be unfalsifiable.
**Evidence after:** `uv run pytest -q` green including
`test_gold_mechanism_self_classifies` (the rubric recognizes its own ground
truth) and `test_fault_enum_never_leaks_into_agent_dirs`; gate self-test
"fixture without gold.json caught".
**Next decision it drove:** score the one-prompt baseline on the first captured
scenarios (declared bar: harness + ≥2 scenarios + scored baseline).

## [0] Scaffold — 2026-08-25

**Change:** repo scaffold created before any problem-specific work: the gates
(preflight/checkpoints/postedit + githooks + `tests/test_gates.sh`) and the
initial docs. NOT the eval harness — at this point `evals/` held only a
README, a `.gitkeep` and a stub whose body was `exit 1`; every scoring,
capture and verification module was built after the problem was chosen.
**Why:** the gates enforce the working discipline — verification-first
reporting, secret scanning, no direct commits to main — from the first
commit, before there is any code to protect.
**Evidence after:** `scripts/checkpoints.sh` green on the empty scaffold.
**Next decision it drove:** choose the problem, design the evaluation, and
freeze the scoring spec before writing any solution code.
