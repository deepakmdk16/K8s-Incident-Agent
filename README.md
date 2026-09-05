# K8s Incident Agent

> **Kubernetes incident-diagnosis agent.** It investigates a broken workload
> through kubectl-shaped tools, runs an explicit hypothesis → evidence → verify
> loop, and must cite — and re-execute — the evidence chain behind its root-cause
> verdict before it may assert one. Three arms over a frozen 12-case set:
> rules-only 27/36, one-prompt baseline 30/36, the agent **36/36** — every number
> re-derivable offline with `make verify`. An additive second case set then
> measures where that ceiling actually is (see *Beyond the frozen set*).

## The problem

**Intended user:** the on-call platform/SRE engineer paged about a broken
Kubernetes workload — concretely, the person staring at `CrashLoopBackOff` in an
unfamiliar namespace at 3am with an SLO clock running.

**Current bottleneck:** diagnosis is slow, hypothesis-driven, and
expertise-gated. The root cause hides across dozens of resources — pod status,
events, logs, rollout history, quotas, RBAC, service wiring — and the failing
symptom is routinely far from the cause (a Service selector typo presents as
application timeouts; a ResourceQuota presents as a deployment that never
progresses). Pasting a raw cluster dump into a chat window truncates or buries
the signal; runbooks and deterministic checkers (e.g. k8sgpt's built-in
analyzers) cover the textbook cases but fail exactly on the overlapping-fault
and red-herring incidents that actually page a human. So the scarce resource —
a senior engineer's evidence-gathering loop — gets spent at 3am, on every
rotation, in every team that runs Kubernetes.

**Why solving it matters:** time-to-root-cause is the dominant, compressible
share of MTTR for workload incidents, and it is currently bought with senior
on-call attention — the most expensive, least scalable input a platform team
has. An agent that runs the same hypothesis → evidence → verify loop over
kubectl-shaped tools, and must cite the evidence behind its verdict, turns a
lengthy expert investigation into a reviewable report the on-call engineer can
confirm in minutes. The human stays the decision-maker: the agent proposes a
diagnosis and remediation plan; only a person approves any change to the
cluster.

## What this is

- `solution/` — the agent: it investigates a cluster through scoped
  kubectl-shaped tools (replaying recorded cluster-state fixtures), runs an
  explicit hypothesis → evidence → verify loop, and must cite the evidence chain
  for its root-cause verdict before it may assert one. Its report carries an
  investigation ledger (differentials ruled out, with evidence), a discrete
  calibrated verdict (`confirmed`/`probable`/`inconclusive` — criteria-defined,
  never a percentage), and a 2-3 command verification recipe so the on-call
  engineer confirms in minutes. Remediation is proposed, never executed — a
  human approves any change to a cluster. Design:
  [solution/README.md](solution/README.md).
- `baseline/` — the simple one-prompt reference arm, frozen once working: a
  curated `kubectl` dump in, one model call, report + answer out. The agent's
  improvements over it are better tools and verification. See
  [baseline/README.md](baseline/README.md).
- `ablation/` — a rules-only control arm: 12 k8sgpt-style analyzers over the
  same fixtures and the same scorer, no model. It exists to test "couldn't a
  decision tree do this?" as a measurement rather than an argument. See
  [ablation/README.md](ablation/README.md).
- `evals/` — the harness that scores all three arms from recorded fixtures with
  one frozen scorer (`evals/scenarios/`, `evals/scoring.py`), plus an additive
  case root (`evals/scenarios-v2/`) scored by an additive scorer
  (`evals/scoring_v2.py`) so new, harder cases never move a frozen number. See
  [evals/README.md](evals/README.md) and
  [evals/scenarios-v2/README.md](evals/scenarios-v2/README.md).
- `common/` — the shared kernel (structured logging, the pinned model call
  shape).
- [CHANGELOG.md](CHANGELOG.md) — the improvement narrative, each entry tied to
  the evidence that drove the next decision.

**From replay to production.** In production this loop hangs off the incident
webhook (Alertmanager/PagerDuty): the alert text is the `page.txt`, the agent
investigates, and the link the paged engineer opens is the report. Only the
transport changes — every tool reads through one module
([solution/fixture.py](solution/fixture.py)), so a live-kubectl adapter is a
bounded swap that touches neither the loop, the validation gate, nor the report
contract. The project replays recorded incidents instead, deliberately: that is
what makes every reported number reproducible from a clean clone with no
cluster and no API key for the offline paths, and it keeps anything that could
touch a real system sandboxed behind human approval.

## Results

Three arms, one frozen 12-case set, three replicate runs each (36 rows per arm),
one frozen scorer. `make verify` re-derives every scored cell below from the
committed evidence bundles offline, in about a second, and fails if the
solution arm misses its declared bar in `evals/reported.json` (pooled ≥36/36,
resource identification ≥36/36, confirmed-wrong ≤0, beats both other arms,
rules failing on ≥3 cases, mean spend ≤$0.36/case, mean latency ≤88s/case).
The rules and baseline cells are re-derived and printed for side-by-side
comparison; they are not independently asserted against this document. Cost and
latency are re-derived per case from each bundle's committed `metrics.json`;
the full-matrix cost row is that per-case mean times the 36 rows, and matches
`totals.cost_usd` in each bundle's `summary.json`.

| metric | rules-only | baseline | **solution** |
|---|---|---|---|
| root-cause identification | 27/36 | 30/36 | **36/36** |
| T1 | 12/15 | 15/15 | **15/15** |
| T2 | 9/15 | 10/15 | **15/15** |
| T3 | 6/6 | 5/6 | **6/6** |
| resource identification | 33/36 | 33/36 | **36/36** |
| right object, sentence unmatched | 3 | 3 | **0** |
| confirmed-wrong (said `confirmed`, was wrong) | 3 | 3 | **0** |
| mean cost / case | $0.0000 | $0.1163 | **$0.1807** |
| mean duration / case | 0.0s | 43.0s | **44.1s** |
| cost, full matrix | $0.00 | $4.19 | $6.51 |

The solution identifies the root cause in **every** run of **every** case, and
its calibration is exact: it never once asserted `confirmed` and was wrong. It
buys those six points over the baseline at 1.55x the spend per case ($0.1807 vs
$0.1163) and effectively the same wall-clock (44.1s vs 43.0s). Both are now
asserted by the same gate, so no future arm can trade an order of magnitude of
cost for the same score unnoticed.

The metric is root-cause identification rate under a frozen scorer, reported
per difficulty tier over 3 replicate runs, with calibration (`confirmed-wrong`)
as the trust metric — specified in [evals/scoring.md](evals/scoring.md) before
any arm was scored. Human time per incident is deliberately **not** reported:
no human trial was run, and an invented figure in a table of measured ones
would be worse than the gap.

### What the agent actually produces

The deliverable is a report an on-call engineer can act on without redoing the
investigation. This is the **committed output** for `t2-rbac-sync-forbidden` —
the same case the rules-only arm returns nothing at all on — excerpted from
[`evals/results/20260829T090941Z-solution/run1/t2-rbac-sync-forbidden/report.md`](evals/results/20260829T090941Z-solution/run1/t2-rbac-sync-forbidden/report.md).
No API key is needed to read it; it is evidence from the scored 36/36 run
(condensed for length — sentences are shortened, wording is the report's own;
the linked file is the verbatim artifact).

> ## Root cause
>
> The inventory-sync worker authenticates as ServiceAccount
> `inventory/inventory-sync` (its pod log prints `serviceaccount=inventory-sync`),
> but the only RoleBinding in the namespace, `inventory/inventory-reader-binding`,
> names the subject `inventory-synk` — a ServiceAccount that does not exist.
> Because no binding grants `Role inventory/inventory-reader` to the identity the
> pod actually runs as, every read is rejected with `HTTP/1.1 403 Forbidden` and
> it falls back to `serving stale inventory snapshot`. The pod stays Running and
> Ready, so the deployment looks healthy while the storefront feed is frozen.
>
> Remediation: edit RoleBinding `inventory/inventory-reader-binding`, field
> `subjects[0].name`: `inventory-synk` → `inventory-sync`.
>
> ## Evidence chain  *(7 items, each citing the exact tool call)*
>
> 4. **[defect]** The only RoleBinding names a subject that is not the pod's service account
>    source: `get_object({"kind": "rolebindings", "namespace": "inventory"})` — verified
>    > `"name": "inventory-synk"`
>
> ## Investigation ledger  *(4 differentials, each ruled out with evidence)*
>
> - Pod crash, restart loop, or scheduling failure — **ruled out**: the pod is
>   Running and Ready with zero restarts, so the failure is authorization, not lifecycle
>
> ## Verification recipe  *(re-executed, not predicted)*
>
> 1. `kubectl get rolebinding inventory-reader-binding -n inventory -o jsonpath='{.subjects[0].name}'` — expect `inventory-synk`  **[PRESENT]**
> 2. `kubectl get deploy inventory-sync -n inventory -o jsonpath='{...serviceAccountName}'` — expect `inventory-sync`  **[PRESENT]**
> 3. `kubectl logs -n inventory inventory-sync-... --tail=50` — expect `403 Forbidden`  **[PRESENT]**
>
> Verdict: **confirmed.**

Every quote is re-checked against the tool output it cites before the report may
assert a verdict, and each verification command is actually re-run against the
captured cluster state — `[PRESENT]` is a measured result, not a prediction. The
design behind that loop is in [solution/README.md](solution/README.md).

### Prior art, and why the ablation is here

[`k8sgpt`](https://github.com/k8sgpt-ai/k8sgpt) is the closest existing tool: a
set of per-resource analyzers that detect broken Kubernetes objects and hand the
findings to an LLM to phrase. The evaluation is deliberately built to be harder
than what that shape of tool can do, and the `rules-only` column measures the
claim instead of asserting it — its 12 analyzers are the k8sgpt shape, given the
paged namespace for free and allowed to name the object whose spec must change.

It reaches 27/36, and the three cases it fails in every run each break it a
different way: it names the right object but the downstream symptom rather than
the cause; it returns *nothing at all* when the fault is a one-character typo in
a RoleBinding subject, because the reference it navigates by is the reference
that is broken; and it says `confirmed` while wrong, having no notion of doubt.
5 of 12 cases fire more than one analyzer, resolved by precedence alone on no
evidence — including one it gets right only because of the order chosen for it.
Full analysis, pre-registered before the arm was written:
[docs/experiments/2026-08-29-rules-ablation.md](docs/experiments/2026-08-29-rules-ablation.md).

## Beyond the frozen set: the ceiling, measured

36/36 says the frozen set is saturated; it does not say what the agent cannot
do. Three additive cases under `evals/scenarios-v2/` were built to find out,
each pre-registered with per-arm predictions before it was scored
([docs/experiments/](docs/experiments/)), and each read from the per-row
sub-scores (`resource_correct`, `class_correct`, verdict) rather than the pooled
number — because twice a pooled gap turned out to be the rubric's vocabulary,
not capability ([CHANGELOG [13], [14]](CHANGELOG.md)).

| case | what it adds | result |
|---|---|---|
| `t2-crossns-externalname-selector` | cause one namespace away from the page | both model arms name the right object 3/3; it tests citation discipline, not search |
| `t3-crossns-decoys` | the same objects under a noise pack with three broken-but-irrelevant decoys | neither arm is misled; the agent never even reads the decoys |
| `t2-checkout-release-stalled` | cause is a **cluster-scoped** orphaned admission webhook configuration, an object no arm's tools can read | **the ceiling** — table below |

The third case is the one that bit. An orphaned `ValidatingWebhookConfiguration`
with `failurePolicy: Fail` makes the API server refuse every pod create; the
paged release stalls; nothing in the paged namespace is wrong. The fixture
carries the object (the capture roster grew to include the kind), but the
baseline's dump policy never includes cluster-scoped objects and the agent's
`get_object` refuses them by design. Scored 2026-09-05, three runs per model arm
([bundles](evals/results/)):

| arm | pooled | resource_correct | class_correct | confirmed-wrong |
|---|---|---|---|---|
| rules-only | 0/1 | 0/1 | 0/1 | 1 |
| baseline | 0/3 | 0/3 | 3/3 | **3** |
| agent (unchanged) | 1/3 | 3/3 | 1/3 | **0** |

Read carefully, that table says three things. The baseline named the kind and
the mechanism every time, could not name the object, and said `confirmed` three
times. The agent's 3/3 on the object was **not** capability: in every run the
name first appears in a validator rejection message ("Present:
workload-standards") after the same guess the baseline made — a leak in the
gate, pre-registered as one before the run. And the verdict gate held:
`confirmed-wrong` 0 against 3, on the same unreadable object.

The leak, and three more found when the fix was reviewed and then attacked,
are closed in [CHANGELOG [16]](CHANGELOG.md): the gate no longer lists names for
kinds no tool serves, no longer accepts a tool's "I cannot serve that" as
evidence, anchors a defect only on a real result about the failing kind, and
refuses argument keys a tool never consumed. A standing test replays all 36
frozen accepted submissions through the current gate and requires every one to
still earn `confirmed`, so hardening the gate cannot silently move the headline.
The pre-registered re-score under the hardened gate (prediction: the agent names
nothing it cannot read, `confirmed-wrong` stays 0) and the slice that then serves
the webhook kinds are the next two steps; both are written down before they run
([docs/experiments/2026-09-04-webhook-outage.md](docs/experiments/2026-09-04-webhook-outage.md)).

## Reproducing the numbers

Written for a clean environment, from a fresh clone.

### Prerequisites

- OS: macOS or Linux · Runtime: **Python 3.12** (pinned in `.python-version`),
  managed by [uv](https://docs.astral.sh/uv/) ≥ 0.11 — the only tool you install;
  it provisions the interpreter and the locked dependencies (`uv.lock`).
- `uv sync` — creates `.venv/` and installs exact pinned versions.
- Copy `.env.example` → `.env` and fill the required keys (only needed for the
  live model arms — the offline paths need no key).
- `bash scripts/preflight.sh` — verifies tools, versions, and keys before
  anything runs.

### Start here: free and offline

```sh
make verify       # re-derive every scored number from the committed bundles (~1 s, $0)
make eval-rules   # run the rules-only arm end to end — no model, no key, <1 s
```

`make verify` runs `evals/run.sh`, which re-scores the committed evidence
bundles named in `evals/reported.json` with the frozen scorer
(`evals/scoring.py`) and asserts the solution arm against its declared bar. The
committed bundles under `evals/results/` are the ones every number in this
README and in `CHANGELOG.md` is quoted from, so the claims verify without a
re-run.

### Run the arms live

All three arms run the same frozen 12-case set from the same recorded fixtures,
and are scored by the same frozen scorer. The two model arms need an
`ANTHROPIC_API_KEY` in `.env` (plus `ANTHROPIC_WORKSPACE_ID` if your key is
identity-linked); the pinned model is `claude-opus-5` (see `common/llm.py`).

```sh
make eval-baseline   # uv run python -m evals.run_eval --arm baseline --runs 3
make eval-solution   # uv run python -m evals.run_eval --arm solution --runs 3
make eval            # both model arms (~$10.7, ~50 min)

# one case, verbose, no scoring — the cheapest live look at the agent:
make demo            # CASE=t2-rbac-sync-forbidden by default
uv run python -m solution.agent \
  --fixture evals/fixtures/t2-rbac-sync-forbidden --out .work/one-case
```

**Expected output:** each invocation prints a scored table (overall, per
difficulty tier, per verdict, and the `confirmed-wrong` calibration count) and
writes a complete evidence bundle to `evals/results/<UTC-stamp>-<arm>/`:
`summary.md`, `summary.json`, `rows.jsonl` (one scored row per case-run), and per
case-run `report.md`, `answer.json`, `metrics.json` — plus `prompt.txt` for the
two model arms (the rules arm builds no prompt), and `system.txt` and
`transcript.jsonl` for the solution arm.

**Required data:** none to obtain — the 15 recorded cluster snapshots (the
frozen 12 plus the three additive cases) ship in the repo under
`evals/fixtures/` (~34 MB). They were captured by `evals/capture.sh` from
disposable local `kind` clusters built by `evals/inject.sh`; they contain no
third-party, personal, or production data, and Secret values are replaced with
`REDACTED-BY-CAPTURE` at capture time. The evaluation replays entirely offline
from those files: **no Kubernetes cluster is needed to reproduce any number
here.**

The additive cases run the same way, scored by the additive scorer:

```sh
uv run python -m evals.run_eval --arm rules --runs 1 --scenarios-root evals/scenarios-v2
uv run python -m evals.run_eval --arm solution --runs 3 --scenarios-root evals/scenarios-v2 \
  --cases t2-checkout-release-stalled
```

**Approximate runtime:** ~25 min per arm for the full 12x3 matrix (~42 s per
case-run, both model arms), on a laptop with no cluster.
**Approximate cost:** **$4.19** baseline / **$6.51** solution / **$0.00** rules
for a full 12x3 matrix — measured, not estimated (~$0.116 and ~$0.181 per
case-run for the two model arms). All three figures are
`cost_usd` summed from the per-case `metrics.json` in the committed bundles,
priced at the pinned model's published rates with cached input billed at its own
rate.

## The instructive failure

**A diagnoser that navigates by reference is blind to a broken reference — and
its silence reads as health.**

The rules-only control arm (`ablation/`, 12 k8sgpt-style analyzers, no model)
was built to measure the "couldn't a decision tree do this?" objection instead
of arguing about it. It scores **27/36**, below even the one-prompt baseline's
30/36. The instructive loss is `t2-rbac-sync-forbidden`, where it returned
**zero findings** — not a wrong answer, no answer at all.

Its RBAC analyzer greps the pod log for a 403 (found), reads the pod's
ServiceAccount (found), then looks for the RoleBinding naming it — and finds
nothing, because the injected fault is that the binding names `inventory-synk`
against a pod running as `inventory-sync`. **The edge it must traverse is the
edge the fault removed.** Diagnosing it requires a claim about something absent
("this binding points at a ServiceAccount that does not exist"), and pattern
matching can only ask about objects that are present. Absence has no signature
to match.

That generalises well past RBAC: dangling references are a large share of real
Kubernetes incidents — a Service selecting labels no pod carries, a volume
naming a deleted ConfigMap, a PVC naming a renamed StorageClass — and they are
precisely where a static analyzer's traversal runs out of graph and goes quiet.
The agent gets this case in all three runs, because hypothesising about what
*should* exist and then checking is a different operation from matching what
does.

The tempting repair — "flag any RoleBinding whose subject ServiceAccount is
missing" — was deliberately not made: it would only have been written *because
we had already read the case*, and an engine that grows one analyzer per case it
has already failed is a lookup table wearing a decision tree's clothes. The
method was pre-registered before the arm was written
([docs/experiments/2026-08-29-rules-ablation.md](docs/experiments/2026-08-29-rules-ablation.md)),
and `make verify` fails if the ablation ever stops failing on at least 3 cases,
so this claim cannot quietly go stale.

**Runner-up, same evidence:** 5 of the ablation's 12 cases fire more than one
analyzer, and in every one the losers are discarded by precedence order alone,
on no evidence — including one T3 case it gets *right* only because we happened
to rank config errors above OOM. A fixed precedence cannot know which of several
simultaneous true observations the page is about. Its 6/6 on T3 is a coin that
landed right twice, and the bundle records the coin.

Full log of observed failure modes, each with the prevention now in place:
[docs/failure-modes.md](docs/failure-modes.md).

## Known limitations

Stated here because the numbers above are only as strong as their caveats;
details and rationale in [solution/README.md](solution/README.md).

- **The prompt is partially fitted to the case set.** The system prompt's
  mechanism-writing examples reuse real strings from the eval fixtures, because
  the rules they illustrate were derived from measured failures on those exact
  rows. The examples illustrate *how to spell*, never *what is wrong* — no
  example states a cause, and every quoted string in a report must re-verify
  against tool output produced in that run — but 36/36 on this set is a claim
  about these 12 cases and the mechanism classes they cover, not a
  generalisation guarantee. New
  capability claims require new frozen cases, scored before any tuning against
  them.
- **Cluster-scoped objects are invisible to the tools — and now measured.**
  ClusterRoles, ClusterRoleBindings and admission webhook configurations are
  captured (the last two kinds since capture schema 2) but not served by any
  read tool; the agent's tools return an explicit "not served by this
  snapshot" for them, which the gate refuses to accept as evidence. The
  additive webhook case above is exactly a fault living there, and the agent
  cannot read its cause today. Serving those kinds is the next slice.
- **Replay-only.** Every number was produced against recorded fixtures, not a
  live cluster. The live-kubectl adapter is a bounded swap behind
  [solution/fixture.py](solution/fixture.py), but it has not been built or
  measured yet.
- **Human time is unmeasured.** No human trial was run, so the claim that the
  report shortens an engineer's time-to-confirm is an argument from the report
  contract, not a measurement.

The extension roadmap — including live-cluster operation and the cluster-scoped
gap — is in [docs/roadmap.md](docs/roadmap.md).

## Verification

`bash scripts/checkpoints.sh` runs the full deterministic gate (tests, lint,
types, secret scan, evidence checks); it is also the pre-push hook. The
language gates it runs, individually:

```sh
uv run pytest -q              # 402 tests: offline, deterministic, no live LLM calls
uv run ruff check .           # lint (config in pyproject.toml)
uv run ruff format --check .  # formatting
uv run pyright                # type check — strict mode, whole tree
```

House rules the gate enforces rather than trusts: gates are fixed and code
bends (a gate change must be stated explicitly, with the reason, in the commit
that makes it); outcomes are reported as measured — a check that did not
demonstrably pass is inconclusive, never a pass; every diagnosed-and-fixed
issue ships its prevention in the same commit; and capability claims ride on
frozen, pre-registered evaluation cases, never on cases tuned after the fact.
