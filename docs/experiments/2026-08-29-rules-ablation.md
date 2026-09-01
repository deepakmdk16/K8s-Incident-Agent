# Pre-registration — rules-only ablation arm (written BEFORE the arm exists)

Committed before `ablation/rules.py` is written. Design requirement 8
(`docs/decisions/problem-selection.md`) calls for a rules-only diagnoser as a
third comparison arm, ">=3 cases where rules demonstrably fail", to pre-empt the
"a decision tree does this" objection. This file fixes the method and the prediction
first, so the reported outcome cannot be the result of tuning against the score.

## What is being tested

**Claim under test:** the 12-case set requires the hypothesis->evidence->verify
loop, and is not solvable by pattern-matching Kubernetes object state.

**Falsifiable form:** if a generic rules engine scores at or near the solution's
36/36, the claim is false and the case set — not the agent — is what needs
work. That outcome gets reported as-is; it is the reason this file exists.

## Methodology constraint (the part that makes this an ablation, not a strawman)

The rules arm is written by someone who has seen all 12 cases. Two failure modes
follow, and both are barred:

1. **Overfitting** — per-case special-casing would make it a lookup table, and
   a 12/12 lookup table proves nothing about rules engines.
2. **Strawmanning** — deliberately omitting an analyzer to manufacture a failure
   would make the ablation dishonest and the changelog entry worthless.

Binding rules, therefore:

- Every analyzer keys on a **generic Kubernetes failure signature** — one you
  would write having never seen this case set. `k8sgpt`'s analyzer set is the
  reference bar: if k8sgpt (or a competent SRE's runbook) would detect it, the
  ablation gets it.
- **No case ids, namespace names, workload names, or fixture paths** appear in
  `ablation/`. Enforced by a test, not by intention.
- **Where the steelman is ambiguous, the ablation gets the benefit.** It is
  handed the paged namespace parsed from the page header (the same hint both LLM
  arms get in their prompt), and it is allowed to name the object whose *spec
  must change* (ResourceQuota, RoleBinding, Service) rather than the workload
  that visibly broke — the harder, more favourable mapping.
- **No tuning after seeing the score.** The analyzer set and their order are
  fixed by this document. If the first scored run disappoints, the result
  stands; a later change ships as its own pre-registered entry.

## The design being fixed now

Namespace-scoped, ordered analyzers; **first match wins**; every finding is
recorded so the ambiguity is measurable.

Analyzer order (most-specific object state first — the order k8sgpt-style
config analyzers run in, and the one most favourable to the arm):

1. container waiting `CreateContainerConfigError` / missing configMap or secret ref
2. container waiting `ImagePullBackOff` / `ErrImagePull`
3. init container not ready
4. container `lastState.terminated.reason == OOMKilled`
5. container waiting `CrashLoopBackOff`
6. pod `Pending` with an unschedulable condition
7. PVC not `Bound`
8. ResourceQuota at or over a hard limit
9. readiness probe failing (pod not ready + probe event)
10. Service whose endpoints have no subsets
11. `Forbidden` / 403 in a pod log, mapped to the RoleBinding of the pod's SA
12. Deployment rollout not progressing

**The ordering is the load-bearing weakness and is the point.** No fixed
precedence can know which of several simultaneous symptoms is the one the page
refers to. Ordering 1-before-4 is the *favourable* choice on the case where it
matters; the honest measurement is therefore not the score alone but the
**ambiguity count** — cases where more than one analyzer fired — which is
order-independent.

## Predictions (recorded before the arm runs)

| case | tier | predicted | reasoning |
|---|---|---|---|
| t1-crashloop-missing-env | T1 | correct | crashloop signature is direct |
| t1-imagepull-bogus-tag | T1 | correct | `ImagePullBackOff` is direct |
| t1-oom-cache-warmup | T1 | correct | `OOMKilled` is direct |
| t1-pvc-storageclass-typo | T1 | correct | PVC `Pending` is direct |
| t1-unschedulable-cpu-requests | T1 | correct | unschedulable condition is direct |
| t2-init-wait-for-migrations | T2 | correct | init container not ready is direct |
| t2-quota-blocks-scale | T2 | **uncertain** | no pod-level symptom; needs the quota object, not the workload |
| t2-rbac-sync-forbidden | T2 | **uncertain** | 403 lives only in logs; needs SA->RoleBinding mapping |
| t2-readiness-wrong-port | T2 | correct | probe failure is direct |
| t2-selector-drift-empty-endpoints | T2 | correct | empty endpoints is direct |
| t3-overlapping-config-and-oom | T3 | **correct but arbitrary** | two findings fire; correct only because rule 1 precedes rule 4 |
| t3-quiet-selector-loud-crashloop | T3 | correct | namespace scoping removes the loud decoy; empty endpoints then fires |

**Headline prediction: 9-12 of 12, with at least 2 cases carrying more than one
firing analyzer.** This is deliberately a *high* prediction. The expected
finding is NOT "rules score badly" — it is that the score overstates the
engine, because the arm is handed the paged namespace, the answer schema, and a
precedence order chosen with hindsight, and it still cannot justify, verify, or
choose between competing findings.

## What the result means either way

- **Rules score low** — design requirement 8 is satisfied directly; the >=3
  failing cases are named.
- **Rules score high** — reported as-is, and the argument moves to what the
  score cannot capture: no evidence chain, no ruled-out alternatives, no
  verification recipe, no calibrated verdict, and no ability to arbitrate
  between simultaneous findings. That is a weaker but honest position.

Either way the number is reported before the interpretation.

---

# Outcome (appended after the run; nothing above this line was edited)

Bundle: [`evals/results/20260829T101751Z-rules/`](../../evals/results/20260829T101751Z-rules/summary.md)
— **27/36 pooled, 9/12 in every one of the three runs, $0.00, 0.4 s.**
Byte-identical answers across runs, as a deterministic arm should be.

| metric | rules | baseline | solution |
|---|---|---|---|
| root-cause identification | 27/36 | 30/36 | **36/36** |
| T1 | 12/15 | 15/15 | **15/15** |
| T2 | 9/15 | 10/15 | **15/15** |
| T3 | **6/6** | 5/6 | **6/6** |
| resource identification | 33/36 | 33/36 | **36/36** |
| right object, sentence unmatched | 3 | 3 | **0** |
| confirmed-wrong | 3 | 3 | **0** |

The headline prediction (9-12) was correct at its **bottom** edge. The two cases
flagged `uncertain` both failed. One case predicted `correct` failed, and it is
the most interesting row in the table.

## The three cases rules demonstrably cannot do

Each fails for a *different* structural reason, and none is a missing analyzer.

**1. `t1-pvc-storageclass-typo` — cause/symptom inversion.** Three analyzers
fired. The arm named the right object (`statefulset/analytics/metrics-db`,
`resource_correct=True`) and then described the wrong thing about it: it
reported the pod as unschedulable when the pod is unschedulable *because* its
PersistentVolumeClaim never bound. Both analyzers are correct observations; only
one is the cause. Precedence order picked the symptom, and precedence order has
no way to prefer a cause — it does not know which observation explains which.

**2. `t2-rbac-sync-forbidden` — the broken-reference blind spot.** The arm
returned **zero findings** and `inconclusive`. Its RBAC analyzer finds the 403 in
the pod log, reads the pod's ServiceAccount (`inventory-sync`), and looks for the
RoleBinding that names it. No RoleBinding does: the injected fault is that
`inventory-reader-binding` binds `inventory-synk` — one character off. The
traversal the analyzer needs is exactly the edge the fault removed, so it finds
nothing and reports nothing. **A rules engine that navigates by reference cannot
diagnose a broken reference.** It would need to notice that a RoleBinding points
at a ServiceAccount that does not exist, which is a hypothesis about what is
*missing*, not a match against what is present. The solution arm gets this case
in all three runs.

**3. `t2-quota-blocks-scale` — right object, sentence unmatched.** The arm named
the correct object (`resourcequota/checkout/checkout-quota`) and its canned
sentence — "The ResourceQuota in this namespace is exhausted, so new pods are
rejected" — matched no fault class under the frozen scorer, because the scorer's
signature needs the standalone word `quota` and the sentence only contains it
inside `ResourceQuota`. **Disclosed plainly: this is a wording artifact, not a
reasoning failure.** It is left uncorrected because the pre-registration bars
tuning after seeing the score, and it is reported in its own row
(`right object, sentence unmatched`) rather than being counted as understanding
the arm does not have. Granting it, the arm would score 30/36 — exactly tied with
the one-prompt baseline, and still 6 rows behind the solution.

## The finding the score does not show

**5 of 12 cases had more than one analyzer fire**, and in every one the losers
were discarded by precedence alone, on no evidence. That includes
`t3-overlapping-config-and-oom`, which the arm gets **right**: three analyzers
fired, and the config-ref rule outranks the OOM rule only because this document
put it there. Order the two the other way — an equally defensible severity-first
runbook — and the arm confidently returns the OOM'd report worker, which is real,
loud, and not what the page is about. The 6/6 on T3 is not robustness; it is a
coin that landed the right way up twice, and the bundle records the coin.

The calibration column is the same story stated in the arm's own voice: it said
`confirmed` on `t2-quota-blocks-scale` and was wrong. It has no notion of doubt,
so it cannot report one. The solution arm's confirmed-wrong is 0/36.

## What this settles

The claim under test survives, with its honest boundary marked. Rules reach
**27/36 on a case set they were given the paged namespace for**, and the 9 they
get are the 9 where a single object's status field already contains the answer.
They lose exactly where diagnosis stops being lookup: choosing among simultaneous
true observations, and reasoning about an absent reference. Both are what the
hypothesis -> evidence -> verify loop is for.

**Adopted as the third comparison arm** — no changes to the arm, per the no-
tuning rule. `evals/reported.json` now pins this bundle and `make verify`
re-derives all three columns offline, failing if the ablation ever stops failing
on at least 3 cases (design req 8, mechanised).
