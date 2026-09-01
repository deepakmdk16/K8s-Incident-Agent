# `ablation/` — the rules-only comparison arm

A deterministic, non-LLM diagnoser: twelve k8sgpt-style analyzers over the same
recorded fixtures, scored by the same frozen scorer as the other two arms. It
exists to answer "couldn't a decision tree do this?" with a number instead of an
opinion (design requirement 8).

**It is not a candidate solution.** It is the control.

```sh
make eval-rules     # 12 cases x 3 runs, offline, $0.00, under a second
```

## Result

**27/36** (9/12, identical in all three runs) against baseline 30/36 and
solution 36/36. Full analysis, including the three cases it cannot do and the
one failure that is a wording artifact rather than a reasoning gap:
[`docs/experiments/2026-08-29-rules-ablation.md`](../docs/experiments/2026-08-29-rules-ablation.md).

## How it is kept honest

The arm was written by someone who had already seen all 12 cases, which invites
both overfitting and strawmanning. Four constraints, fixed in the
pre-registration before the arm existed and enforced by
`tests/test_rules_ablation.py`:

1. **Generic signatures only.** Every analyzer keys on a Kubernetes failure
   signature you would write having never seen this case set; `k8sgpt`'s
   analyzer set is the reference bar.
2. **No case knowledge.** No case id, scenario namespace, or fixture workload
   name appears in any string literal here (`test_ablation_never_learns_the_case_set`).
3. **Ambiguity resolved in the arm's favour.** It is handed the paged namespace
   (the same hint both LLM arms get) and is allowed to name the object whose
   *spec must change* — ResourceQuota, RoleBinding, Service — rather than the
   workload that visibly broke.
4. **No tuning after the score was seen.** The analyzer list and its order are
   as pre-registered.

It also cannot import `common.llm` or `evals.scoring`, so it can neither call a
model nor answer to the grader.

## The deliberate weakness

Analyzers run most-specific-first and **the first match becomes the answer** —
but every finding is recorded, in the report's evidence chain and in
`metrics.json` (`analyzers_fired`). This is not a shortcut: no fixed precedence
can know which of several simultaneous symptoms the page refers to. 5 of the 12
cases fire more than one analyzer, and in each the losers are dropped by
ordering alone, on no evidence. The report says so in those words.
