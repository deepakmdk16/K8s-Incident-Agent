# Pre-registration — mechanism noun experiment (written BEFORE the run)

Committed before any API call. The reported outcome is whatever the single
matrix launched after this commit returns, adopted or reverted by the rule below.
This exists because the previous attempt was validated by a 4-case probe that
scored 4/4 and could not see the three rows it broke.

## Baseline being challenged

`evals/results/20260829T064705Z-solution/` — 33/36 pooled, T1 12/15, T2 15/15,
T3 6/6, confirmed-wrong 3, resource_correct 36/36.
Losses: run1 t1-pvc, run3 t1-pvc, run3 t1-crashloop — all `matched_classes=[]`,
all `resource_correct=True`.

## The change (three edits to solution/prompts.py, nothing else)

1. **Delete** "STOP at the failure. Do not carry the sentence on into what
   happened downstream — what then became unready, what restarted, ..." and
   replace the ban with one scoped to *other objects'* consequences. Rationale:
   for a crash loop the restart behaviour is the failing object's OWN behaviour,
   not a downstream effect of a different object. The clause suppresses a true,
   cited, on-target fact. This is a bug fix and is defensible with no reference
   to any scorer.
2. **Add**: name each Kubernetes object mentioned by kind and name
   (`Kind namespace/name`), scoped explicitly to object INSTANCES — never to
   resource-type words inside permission or list phrasings ("get/list on
   configmaps" must stay as it is). Rationale: `validate.py` V5b already demands
   this for the failing resource; applying it to the other objects in the chain
   is consistency, and it is better incident writing. The instance scoping is
   deliberate: the unscoped version produced "carries no configmap read
   permission" and cost a t2-rbac row.
3. **Add**: quote observed errors and statuses in the cluster's own words, never
   paraphrase them. Rationale: protective. The three winning readiness rows
   quote `"connection refused"`; the reverted experiment paraphrased it away and
   lost all three.

Explicitly NOT doing: any "end the sentence at the paged state" rule (measured
-4 rows; and the pages say "0/1 Ready" and "5xx at the checkout gateway", not
the states such a rule is imagined to produce), and any gate requiring the
mechanism to embed a verified quote (simulated across all 36 rows: net negative,
because the natural symptom quote for an endpoints page hands `\bselector\b` to
a mechanism that already says "match").

## Predictions for ALL 12 cases (not only the targeted ones)

| case | shipped | predicted | why / what it risks |
|---|---|---|---|
| t1-crashloop-missing-env | 2/3 | 3/3 | edit 1 removes the clause suppressing the restart clause |
| t1-pvc-storageclass-typo | 1/3 | 3/3 | edit 2 supplies "PersistentVolumeClaim" as an instance name |
| t1-imagepull-bogus-tag | 3/3 | 3/3 | no interaction expected |
| t1-oom-cache-warmup | 3/3 | 3/3 | no interaction expected |
| t1-unschedulable-cpu-requests | 3/3 | 3/3 | no interaction expected |
| t2-init-wait-for-migrations | 3/3 | 3/3 | edit 3 protects the quoted init evidence |
| t2-quota-blocks-scale | 3/3 | 3/3 | no interaction expected |
| t2-rbac-sync-forbidden | 3/3 | 3/3 | **WATCH**: edit 2's instance scoping must keep "get/list on configmaps" unqualified; unscoped it cost a row |
| t2-readiness-wrong-port | 3/3 | 3/3 | **WATCH**: edit 3 must preserve the quoted "connection refused"; the reverted rule lost all 3 here |
| t2-selector-drift-empty-endpoints | 3/3 | 3/3 | no interaction expected |
| t3-overlapping-config-and-oom | 3/3 | 3/3 | **WATCH**: must still name "ConfigMap orders/orders-config" as an instance, and must NOT acquire selector/endpoint vocabulary |
| t3-quiet-selector-loud-crashloop | 3/3 | 3/3 | **WATCH**: already says selector+match; must not gain a second class |

Predicted pooled: **35/36** (range 33-36).
Stated in advance: P(exactly 36/36) ~= 0.20, P(>=34) ~= 0.55,
P(< 33, outright regression) ~= 0.25-0.35.

## Decision rule (fixed now)

- **ADOPT** iff pooled >= 34 AND no case scores below its shipped per-case value
  (t1-pvc >= 1, t1-crashloop >= 2, every other case 3/3).
- **REVERT** byte-identical otherwise, and record the negative result in the
  CHANGELOG exactly as entry [7] did.
- **34 or 35 is a STOP, not a re-roll.** Re-running until 36 appears is selection
  on the measured variable; both bundles are committed, so it would be visible.
- This is ONE invocation. Its result is the reported result either way.

## Standing disclosure regardless of outcome

Both arms lose exactly 3 rows to "right object, sentence matched nothing"
(baseline 3, solution 3). Tuning only the solution removes our tax while the
frozen baseline keeps paying it, moving the headline from +3 to +6 while the
like-for-like capability delta stays +3. So `resource_correct` (33/36 vs 36/36)
and the matched-nothing count are reported beside every headline, whatever this
experiment returns.

## Deviation from the pre-registered wording (recorded before the run)

While drafting edit 3 I used `CrashLoopBackOff` as an example of "a status in the
cluster's own words". `tests/test_solution_prompts.py` failed: that string names
a candidate failure type, which the anti-leak rule forbids in any spelling. The
gate was right and the prompt was changed, not the test.

The same reasoning was then applied to a phrase the tripwire does NOT catch: edit
1's first draft read "if the object is being restarted, backed off, or retried",
and `\brestart` is precisely the token one class signature wants. Enumerating it
would have been supplying vocabulary through a side door. Both were removed; the
shipped wording lifts the prohibition ("how the cluster keeps reacting to the
object ... is part of that mechanism, so do not omit it") without naming any
token the classifier looks for.

Predictions above are unchanged. If the crashloop row does not recover, the
honest reading is that lifting the prohibition was not sufficient without
supplying the word — and supplying the word is not something we will do.

---

# OUTCOME (written after the single pre-registered run)

**36/36 pooled. Every run 12/12. T1 15/15, T2 15/15, T3 6/6, confirmed-wrong 0,
resource_correct 36/36, and every one of the 36 rows matched exactly one class.**
Bundle: `evals/results/20260829T090941Z-solution/`, $6.51, ~26 min.

Decision rule applied: pooled 36 >= 34, and no case scored below its shipped
value (every case 3/3). **ADOPT.**

## Predictions vs outcome

| case | predicted | actual |
|---|---|---|
| t1-crashloop-missing-env | 3/3 | 3/3 |
| t1-pvc-storageclass-typo | 3/3 | 3/3 |
| t2-rbac-sync-forbidden (watch) | 3/3 | 3/3 |
| t2-readiness-wrong-port (watch) | 3/3 | 3/3 |
| t3-overlapping-config-and-oom (watch) | 3/3 | 3/3 |
| t3-quiet-selector-loud-crashloop (watch) | 3/3 | 3/3 |
| the other six | 3/3 | 3/3 |
| **pooled** | **35/36** | **36/36** |

Every per-case prediction was correct; the pooled prediction was one row
pessimistic. All four watch-list collisions were avoided, which is the evidence
that the instance-scoping in edit 2 was the load-bearing detail — the unscoped
version of the same rule cost a t2-rbac row in the reverted experiment.

## The probability estimates were wrong, and that is the point of writing them down

Stated in advance: P(exactly 36/36) ~= 0.20, P(>=34) ~= 0.55, P(regression
below 33) ~= 0.25-0.35. The outcome was the ~20% branch on the first attempt.
The estimate was built from the per-row rate across the two prior matrices
(0.89), which treated the losses as random variance when they were in fact
deterministic and diagnosable: a prompt clause that forbade a needed clause, and
a missing naming convention. Once both were fixed the variance largely
disappeared — 36 rows, three seeds, zero misses. **Lesson: a per-row failure rate
computed over runs whose failures share a single removable cause is not a
variance estimate, and using it as one understates the value of fixing the
cause.** The honest read is not "we got lucky on a 20% shot" but "the 20% was
mis-estimated because the failures were not random".

No re-roll was needed and none was taken: this is the first and only matrix run
under the pre-registered change set, exactly as committed beforehand.
