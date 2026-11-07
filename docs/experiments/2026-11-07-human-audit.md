# Human audit of accepted rows — packet prepared 2026-11-07 (STATUS 3.4)

**Status: not yet audited.** This file is the packet and the answer sheet; the
numbers below are filled in by a person, not by a script.

## What it estimates, and why nothing else can

Every headline number says the *grader* accepted a row: V1–V7 re-executed
every citation, the frozen scorer matched the resource and the mechanism
sentence. None of that says a human reading the same evidence would agree. The
sweep ([21], [23]) bounds what an *adversary* can get past the gate; this audit
is the only direct estimate of the gate's false-positive rate on *honest*
rows — rows the agent meant, which the gate accepted, which may still be wrong
in a way no mechanical check sees (a quote that is present but does not show
what the claim says it shows; a mechanism that names the right field for the
wrong reason).

## Method (fixed before the first row is read)

- **Rows:** one `confirmed` row per frozen case — **run 1** of the frozen
  solution bundle `evals/results/20260829T090941Z-solution` (all 36 rows there are `confirmed`; run 1 is
  chosen by rule, not by reading). 12 rows.
- **The auditor reads** the row's `report.md` (root cause, evidence chain with
  quotes, investigation ledger, verification recipe) **against the fixture
  only** — `evals/fixtures/<case>/` — opening whatever files the quotes point
  at. **Not** `gold.json`, not `notes.md`, not the scoring code, until the
  row's verdict is written down. Record the order if it was broken.
- **Four judgements per row,** each `agree` / `disagree` / `unsure`, with a
  one-line reason for anything but `agree`:
  1. *Resource* — the object the report says must change is the one a
     competent operator would edit.
  2. *Mechanism* — the mechanism sentence is true of this fixture and
     explains the paged symptom (not merely consistent with it).
  3. *Quotes* — every quoted string is present where the report says and
     actually supports the claim it is attached to (present-but-irrelevant is
     a `disagree`).
  4. *Verdict* — `confirmed` was earned: the evidence shown rules out the
     listed alternatives, and the auditor sees no unlisted alternative the
     fixture leaves open.
- **The number reported** is the count of rows with four `agree`s, over 12,
  plus the per-judgement counts. A single `disagree` on judgement 3 or 4 is
  the finding this audit exists to make; it is written up per row before any
  pooled number is quoted (CLAUDE.md: a gap names a difference, never its
  cause).
- **After all 12:** open the golds, and for each `disagree` note whether the
  gold agrees with the auditor or with the gate. A row where auditor and gold
  disagree with each other is a *gold* finding and goes to
  docs/failure-modes.md, not here.

## Answer sheet

| tier | case | report | fixture | resource | mechanism | quotes | verdict | notes |
|---|---|---|---|---|---|---|---|---|
| T1 | `t1-crashloop-missing-env` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t1-crashloop-missing-env/report.md) | [`fixture`](../../evals/fixtures/t1-crashloop-missing-env/) |  |  |  |  |  |
| T1 | `t1-imagepull-bogus-tag` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t1-imagepull-bogus-tag/report.md) | [`fixture`](../../evals/fixtures/t1-imagepull-bogus-tag/) |  |  |  |  |  |
| T1 | `t1-oom-cache-warmup` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t1-oom-cache-warmup/report.md) | [`fixture`](../../evals/fixtures/t1-oom-cache-warmup/) |  |  |  |  |  |
| T1 | `t1-pvc-storageclass-typo` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t1-pvc-storageclass-typo/report.md) | [`fixture`](../../evals/fixtures/t1-pvc-storageclass-typo/) |  |  |  |  |  |
| T1 | `t1-unschedulable-cpu-requests` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t1-unschedulable-cpu-requests/report.md) | [`fixture`](../../evals/fixtures/t1-unschedulable-cpu-requests/) |  |  |  |  |  |
| T2 | `t2-init-wait-for-migrations` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t2-init-wait-for-migrations/report.md) | [`fixture`](../../evals/fixtures/t2-init-wait-for-migrations/) |  |  |  |  |  |
| T2 | `t2-quota-blocks-scale` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t2-quota-blocks-scale/report.md) | [`fixture`](../../evals/fixtures/t2-quota-blocks-scale/) |  |  |  |  |  |
| T2 | `t2-rbac-sync-forbidden` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t2-rbac-sync-forbidden/report.md) | [`fixture`](../../evals/fixtures/t2-rbac-sync-forbidden/) |  |  |  |  |  |
| T2 | `t2-readiness-wrong-port` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t2-readiness-wrong-port/report.md) | [`fixture`](../../evals/fixtures/t2-readiness-wrong-port/) |  |  |  |  |  |
| T2 | `t2-selector-drift-empty-endpoints` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t2-selector-drift-empty-endpoints/report.md) | [`fixture`](../../evals/fixtures/t2-selector-drift-empty-endpoints/) |  |  |  |  |  |
| T3 | `t3-overlapping-config-and-oom` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t3-overlapping-config-and-oom/report.md) | [`fixture`](../../evals/fixtures/t3-overlapping-config-and-oom/) |  |  |  |  |  |
| T3 | `t3-quiet-selector-loud-crashloop` | [`report.md`](../../evals/results/20260829T090941Z-solution/run1/t3-quiet-selector-loud-crashloop/report.md) | [`fixture`](../../evals/fixtures/t3-quiet-selector-loud-crashloop/) |  |  |  |  |  |

## Result

Not yet audited. When filled: rows with four `agree`s __/12; per judgement
resource __/12, mechanism __/12, quotes __/12, verdict __/12; auditor, date,
time spent; the per-row write-ups for every non-`agree` above this line.
