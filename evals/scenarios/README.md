# Scenario roster & authoring contract

Binding sources, in order: `docs/decisions/problem-selection.md` (case design,
design reqs 3/4/6, kill conditions), `evals/scoring.md` + `evals/scoring.py`
(gold contract, signatures, dominance), CHANGELOG entry [2] (realistic-
entrypoint lesson). This file applies them to case production; where it could
disagree with those sources, they win. The roster below incorporates the
2026-08-29 three-lens red-team review
(`evals/out/20260829-roster-red-team.json`); its accepted findings are baked
into the case rows and rules rather than tracked separately.

## Authoring contract (every scenario directory)

Each `evals/scenarios/<id>/` ships these files:

| File | Contract |
|---|---|
| `fault.yaml` | Manifests only — the fault is code. Header comment: atom, mechanism, wait condition. Applied by `evals/inject.sh`, never by hand. Includes every healthy dependency the remediation needs (e.g. the correctly-named Service a typo points away from). |
| `setup.yaml` | Optional. Pre-fault healthy state for two-phase scenarios (inject applies it and waits Available before the fault). |
| `wait.sh` | Executable, takes the kubectl context as `$1`. Polls until the fault has observably manifested — the *specific decisive evidence* (event message text, waiting reason + restart count, log line), never just a coarse reason that a different failure could also produce; exits 0 on manifest, 1 on 300s timeout. |
| `page.txt` | The symptom-first page. `[PAGE] SEV<n> <AlertName> — <area>` header, then 3-6 lines naming the paged service and user-visible symptom, ending with the standing ask (root cause + remediation). Never names an object whose spec must change when that object differs from the paged workload; never mechanism or fault-class vocabulary (nothing that matches a `_SIGNATURES` group — no "restarting", "looping", "pull", "quota"). |
| `gold.json` | Schema 1, per `evals/scoring.md`. `failing_resource` = the object whose **spec must change** (owning deployment, not the crashing pod; the Service when the selector is wrong; the ResourceQuota when the quota is wrong). Where two objects could defensibly change (renamed key: producer vs consumer), the fixture must contain the asymmetry that makes gold's side canonical, and `notes.md` states it. `mechanism_summary` must self-classify to exactly `fault_class` through `scoring.classify_mechanism` including dominance collapse — run the check before committing. `remediation_summary` must be executable as written (no edits the API server rejects — e.g. volumeClaimTemplates are immutable: say recreate, not edit). |
| `notes.md` | Tier + status, provenance (inject.sh run date), informal ground truth, wait condition, why the case is deterministic, the gold-side asymmetry if any, and the counterfactual-verification record (rule 6). |

Hard rules:

1. **Counterfactual entrypoints** (CHANGELOG [2]): a container command must
   succeed if the injected misconfiguration is corrected. The fault lives in
   the Kubernetes objects (env/ref/selector/probe/quota/limits), never in a
   script that fails unconditionally — this applies to every container in the
   scenario, decoys included. Emulated apps (busybox `sh` loops, `httpd`) are
   fine; unconditional `exit 1` is not.
2. **Mechanism one hop from the spec (T2/T3)**: non-trivial app logic ships as
   a ConfigMap-mounted entrypoint script (`command: ["sh", "/app/run.sh"]`),
   so `describe pod` shows the wiring but reading the logic takes one more
   investigative step. T1 cases may inline simple conditional commands —
   textbook-easy is their job.
3. **Deterministic in kind, host-independent**: single-node kind, kindnet CNI
   (**NetworkPolicy is not enforced — never a fault atom here**), no
   metrics-server (no live HPA scaling), storageClass `standard` only.
   Unschedulable requests use values no real host satisfies (`cpu: "512"`).
   Failing image pulls reference `registry.k8s.io` (no anonymous rate limits —
   Docker Hub throttling can freeze a fixture whose events say
   `toomanyrequests` instead of the injected fault). Healthy/noise workloads
   use only the node-cached `busybox:1.36`. Ordering matters where admission
   races: a ResourceQuota document precedes the Deployment it must reject,
   with a margin wide enough that the arming window cannot absorb all
   replicas.
4. **Namespace ownership**: a scenario owns every namespace it creates and
   lists them in `notes.md`; `inject.sh` wipes all non-system namespaces
   between scenarios and refuses to run if `default` holds stray objects.
   Only namespaced objects + Namespaces (inject.sh enforces). Realistic names
   (`payments`, `shop`, `inventory`), never `test`/`demo`; workload and
   container **names must not encode the mechanism** (`checkout-worker` is
   fine; `oom-worker` is not).
5. **No leakage**: nothing under `evals/scenarios/<id>/` is ever tool-visible
   to an agent (scoring.md anti-leak invariant 2); the page is the only
   scenario text an arm sees.
6. **Counterfactual verified before capture**: for each case, rehearse the
   fix live before the pristine capture — `inject.sh --no-capture`, confirm
   the fault manifested, apply `gold.json`'s remediation, confirm recovery,
   then wipe and re-inject cleanly for the real capture. Record the rehearsal
   (date, fix applied, recovery observed) in `notes.md`. A remediation that
   was never seen to work is an assertion, not ground truth.
7. **Frozen once captured**: after a fixture is captured and merged, scenario
   files change only with a disclosed re-capture (`inject.sh --force`,
   CHANGELOG'd) until the `case-set-freeze` tag (anchored at this repository's
   initial commit); after the freeze, the frozen paths change only via
   disclosed decision-doc updates, checkable against the tag
   (`evals/scoring.md`).

## Roster (12 cases: 5 T1 / 5 T2 / 2 T3)

Difficulty comes from composition, never exotic machinery: T1 proves the
comparison fair; T2 puts the symptom far from the cause; T3 adds overlap,
red herrings, and cluster noise (~20 filler namespaces via
`evals/scenarios/_noise/`, cached-image only). Metric reported overall + per
tier.

**Tier honesty, stated in advance:** #7 and #10 are expected to be solvable by
the baseline's own curation rule (their decisive evidence sits in describes of
not-Ready pods, which the baseline dump includes) — they calibrate fairness
and gold the readiness/init atoms. The T2 measured-improvement gap is designed
to rest on #6, #8, and #9; the T3 gap on #11 and #12.

| # | id | tier | gold class | page symptom → root cause (distance) |
|---|---|---|---|---|
| 1 | t1-crashloop-missing-env | T1 | app-crashloop | Checkout worker 0/1 Ready → entrypoint requires unset `AMQP_URL`, exits at startup. **captured**; re-captured 2026-08-29 with a conditional entrypoint + wait.sh after the red-team finding (see notes.md). |
| 2 | t1-imagepull-bogus-tag | T1 | image-pull-backoff | Storefront deploy stuck 0/2 → image references a nonexistent tag on registry.k8s.io; containerd `not found` in events (wait.sh greps the event message, not just the ImagePullBackOff reason). |
| 3 | t1-oom-cache-warmup | T1 | oom-killed | Recommendations missing from product pages, deploy 0/1 Ready → cache warmup allocates ~200MiB under a 64Mi limit as the foreground step then serves; OOMKilled (137) each start. Correct limit → warmup completes and the serve loop runs. |
| 4 | t1-unschedulable-cpu-requests | T1 | pod-unschedulable | Fraud-scoring deploy Pending → container requests `cpu: "512"` (unsatisfiable on any host); FailedScheduling Insufficient cpu. |
| 5 | t1-pvc-storageclass-typo | T1 | unbound-pvc | Analytics DB StatefulSet stuck → volumeClaimTemplate names storageClass `fast-ssd` which does not exist; PVC Pending, ProvisioningFailed. Remediation: recreate with `standard` (volumeClaimTemplates immutable — never "edit the template"). |
| 6 | t2-selector-drift-empty-endpoints | T2 | service-selector-mismatch | Storefront 502/timeouts on product pages → catalog Service selector doesn't match pod labels; Endpoints empty while catalog pods are Ready and probes pass. Gateway logs show upstream timeouts. |
| 7 | t2-readiness-wrong-port | T2 | readiness-probe-failing | Orders API 5xx at the gateway → readiness probe targets the wrong port; pods Running-never-Ready, Endpoints empty. (Predicted baseline-solvable — see tier honesty.) |
| 8 | t2-quota-blocks-scale | T2 | resource-quota-exceeded | Checkout latency SLO burn after replicas raised to 6 → ResourceQuota (`pods: "2"`, listed before the Deployment in fault.yaml) rejects the rest; ReplicaSet FailedCreate `exceeded quota` events. wait.sh gates on the event text; admitted-pod count is NOT asserted (admission arming window varies). Fix is the quota → gold = the ResourceQuota. |
| 9 | t2-rbac-sync-forbidden | T2 | rbac-denial | Inventory counts stale >30 min → sync worker's API reads return 403; RoleBinding subject names a **nonexistent** ServiceAccount (typo), making the binding the only defensible fix. Worker logs its SA identity + per-request HTTP codes (ConfigMap-mounted script, rule 2), succeeds on `/api` discovery (proves the token header works — a mis-sent header would 403 as anonymous and fixing the binding wouldn't recover), 403s on the namespaced read. |
| 10 | t2-init-wait-for-migrations | T2 | init-container-failure | Billing release stalled >20 min → billing-api's init container polls a typo'd DB host (`db-primary` vs the real `postgres-primary` Service) forever (nc TCP check, loop-forever design: Init:0/1, init Running, no restarts); the correctly-named healthy DB ships in fault.yaml so the corrected name recovers. Namespace `billing` (payments is owned by case #1); the id's "migrations" is narrative — the wait-for-db gate stands in for a migration-completion check. (Predicted baseline-solvable — see tier honesty.) |
| 11 | t3-quiet-selector-loud-crashloop | T3 **(hard case)** | service-selector-mismatch | Search timeouts → search Service selector mismatch (quiet: pods Ready, no events) while `analytics-batch/report-generator` — in the scenario's own fault.yaml, conditional missing-env failure per rule 1, function verifiably unrelated to search — crashloops loudly. Noise pack active. |
| 12 | t3-overlapping-config-and-oom | T3 | bad-config-ref | Order submission API 5xx, orders-api pods 0/N Ready → orders-api still references renamed ConfigMap key `db_url` (CreateContainerConfigError "couldn't find key"); a second healthy consumer (`orders-audit`) already reads the NEW key successfully, evidencing the rename as intentional → gold = deployment/orders-api (its ref must change). Decoy: `orders-report-worker` (nightly export, verifiably off the submission path) genuinely OOM-crashloops. Noise pack active. |

**Reserve** (swaps in, disclosed, if a roster case fails determinism during
capture): `t2-rollout-paused` — setup.yaml rolls out v1 healthy; fault.yaml
ships the v2 template with `spec.paused: true`; new version never ships, zero
error events; gold class rollout-stuck. Requires the two-phase inject path.

Atom coverage: 11/12 classes appear as gold (kill condition 2 headroom).
`rollout-stuck` is by design a dominated symptom bucket (scoring.md); it only
golds via the reserve case, and stuck rollouts appear as *symptoms* in #10.

## Capture protocol

`evals/inject.sh --id <id>` (see script header): refuse on stray `default`
objects → wipe non-system namespaces → apply `_noise/noise.yaml` (T3 ids) and
wait Available → apply `setup.yaml` if present and wait Available → apply
`fault.yaml` → run `wait.sh` → invoke `capture.sh` → run
`scripts/checkpoints.sh`. One scenario live per capture, ever. Rule 6's
counterfactual rehearsal precedes the pristine capture. Capture order is tier
order (T1 → T2 → T3); each fixture merges together with its scenario directory
so `run_eval` case discovery never sees a gold without a fixture.
