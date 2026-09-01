# t1-crashloop-missing-env — scenario notes

**Tier:** T1 (textbook single fault). **Status:** first captured case; doubles
as the capture-pipeline validation fixture.

**Provenance:** fault hand-injected during the initial kind smoke test
(2026-08-28); `fault.yaml` reconstructed from the live cluster spec afterwards. From
the case-production slice onward, scenarios are applied FROM `fault.yaml`
(manifests + wait condition), never by hand — this one is the bootstrap
exception and is disclosed as such.

**Retrofit 2026-08-29 (red-team finding, roster review):** the bootstrap
command failed UNCONDITIONALLY (`exit 1` regardless of AMQP_URL — the
CHANGELOG [2] anti-pattern this contract's rule 1 bans), so gold's
remediation was never demonstrably true, and the missing wait.sh meant the
sanctioned `inject.sh --force` re-capture path could not run. Retrofit:
conditional entrypoint (AMQP_URL set → consume loop runs), wait.sh added
(restarts >= 3 + CrashLoopBackOff + the FATAL log line), fixture
re-captured via `inject.sh --id t1-crashloop-missing-env --force` on
**2026-08-29 ~08:20 IST** (capture self-check + checkpoints fixture gate
green, 12 fixtures). gold.json unchanged (mechanism and remediation were
already stated correctly; the manifest now actually implements them).

**Counterfactual-verification record (rule 6): 2026-08-29 ~07:55 IST** —
`inject.sh --no-capture --force` manifested the gates (restarts=5,
CrashLoopBackOff, `FATAL: AMQP_URL not set` in logs). Applied gold's
remediation (`set env AMQP_URL=amqp://queue.payments.svc:5672`) →
`deployment "checkout-worker" successfully rolled out`, logs show
`consuming checkout jobs`, ready=true with 0 restarts — the retrofit makes
the original gold remediation demonstrably true for the first time. Wiped
and re-injected cleanly for the --force re-capture.

**Ground truth (informal — the formal, scored version is `gold.json` in this
directory, per `evals/scoring.md`):**

- Failing resource: `payments/deployment/checkout-worker`
- Fault class: CrashLoopBackOff — required config (env var) missing
- Mechanism: container entrypoint requires `AMQP_URL`; it is unset, the
  process logs `FATAL: AMQP_URL not set` and exits 1; kubelet restarts with
  exponential backoff.
- Decisive evidence: current-channel container logs. Note the `--previous`
  channel may legitimately be unavailable on kind/containerd (GC'd exited
  container — see docs/failure-modes.md 2026-08-28); the fixture's
  `scenario.yaml` records per-channel availability.
- Plausible remediation: set `AMQP_URL` on the deployment (env or via
  ConfigMap/Secret ref) to the queue endpoint; roll out.

**Wait condition (for the future scenario runner):** pod with label
`app=checkout-worker` in `payments` has `restartCount >= 3` and waiting reason
`CrashLoopBackOff`.
