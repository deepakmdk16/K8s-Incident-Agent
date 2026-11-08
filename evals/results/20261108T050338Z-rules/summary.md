# Scored run — arm: rules

- started: 20261108T050338Z  |  runs: 1  |  cases/run: 5
- scorer: evals.scoring_v2
- model: none — deterministic rules engine, no API call
- totals: 0 in / 0 out tokens, $0.0000

| run | overall | T1 | T2 | T3 | confirmed | probable | inconclusive | invalid | confirmed-wrong |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2/5 | 1/1 | 1/3 | 0/1 | 1/2 | 1/2 | 0/1 | — | 1 |
| pooled | 2/5 | 1/1 | 1/3 | 0/1 | 1/2 | 1/2 | 0/1 | — | 1 |

## Hedging (pooled; report only, no bar)

| verdict | coverage | accuracy |
|---|---|---|
| confirmed | 2/5 (40.0%) | 1/2 (50.0%) |
| probable | 2/5 (40.0%) | 1/2 (50.0%) |
| inconclusive | 1/5 (20.0%) | 0/1 (0.0%) |

- inconclusive on a readable kind: 1; on an unreadable kind: 0 (readable = solution.tools.serves_kind(gold failing-resource kind): the solution's tool layer, applied to every arm's rows)
