# t2-init-wait-for-migrations — scenario notes

**Tier:** T2 (release-stalled page; fault is one hostname deep in an init
container's env while the real database sits healthy one service away).
**Status:** authored 2026-08-29 (Sat morning); captured and scored in the
frozen case set (fixtures: `evals/fixtures/t2-init-wait-for-migrations/`).

**Predicted baseline-solvable** (roster tier-honesty note): Init:0/1 pods
are not-Ready, so their describes and init logs land in the baseline's own
curation dump; this case calibrates fairness and golds the init atom. The
T2 measured-improvement gap rests on #6/#8/#9.

**Namespaces owned:** `billing` (rule 4).

**Provenance:** authored by the operator from roster row #10 and the
2026-08-29 red-team constraints (real dependency ships in fault.yaml;
loop-forever design chosen and gated explicitly); applied only via
`evals/inject.sh` — run date recorded at capture below.

**Ground truth (informal — the scored version is `gold.json`):**

- Failing resource: `billing/deployment/billing-api`
- Fault class: init-container failure
- Mechanism: init env `DB_HOST=db-primary` vs real Service
  `postgres-primary` → `nc -z` can never succeed (the name resolves to
  nothing) → init loops forever → pods held at Init:0/1.
- Decisive evidence: Init:0/1 with init state.running + the repeating
  `waiting for db-primary:5432` log + the service list showing only
  `postgres-primary`.
- Remediation: patch the init container's DB_HOST (gold has the exact
  command).

**Design points (red-team constraints, encoded):**

- **The healthy dependency ships in fault.yaml** (postgres-primary deploy +
  service, endpoints populated): without it, correcting the typo would
  still fail and gold's remediation would be false. wait.sh gate 3 refuses
  to capture unless the dependency is actually Available.
- **Loop-forever, not probe-and-exit**: the init container stays Running
  with zero restarts and zero failure events — the quiet variant. wait.sh
  gates on state.running + a persistent log loop (>= 6 wait lines ≈ 30s),
  never on restarts (which would time out against this design).
- **The typo lives in the pod spec env**, not inside the script: the script
  is generic (`$DB_HOST`), the wrong value is declarative and visible in
  the describe — where a config typo realistically lives.

**Wait condition:** both pods init-Running + >= 6 `waiting for
db-primary:5432` log lines + postgres-primary Available (wait.sh; 5s poll,
300s cap).

**Why deterministic:** `db-primary` matches no Service, so DNS resolution
fails on every attempt — `nc -z` cannot flake into success; the healthy
listener loop keeps postgres-primary Available; only node-cached
busybox:1.36; no timing races (the gate requires sustained looping, which
is the steady state).

**Rule 2 (one hop):** both containers' logic lives in ConfigMap-mounted
scripts; pod spec carries only wiring + the faulty env value.

**Gold-side asymmetry:** creating a `db-primary` Service aliasing the
database would also unblock the init, but the namespace's own naming
(postgres-primary deploy + service pair) evidences the intended name; the
init env is the single value out of agreement → gold = the deployment.

**Counterfactual-verification record (rule 6): 2026-08-29 ~07:45 IST** —
`inject.sh --no-capture` manifested all three gates (2 pods Init-running;
6 `waiting for db-primary:5432` loop lines; postgres-primary Available).
Applied gold's remediation (patched the init container's DB_HOST to
`postgres-primary`) → `deployment "billing-api" successfully rolled out`,
both replacement pods ready=true. Wiped and re-injected cleanly for the
pristine capture.
