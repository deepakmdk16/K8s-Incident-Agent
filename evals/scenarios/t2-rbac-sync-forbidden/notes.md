# t2-rbac-sync-forbidden — scenario notes

**Tier:** T2 (freshness page on the storefront; fault is a one-letter typo
in a permissions object that emits no events at all). **Status:** authored
2026-08-29 (Sat morning); captured and scored in the frozen case set
(fixtures: `evals/fixtures/t2-rbac-sync-forbidden/`).

**Namespaces owned:** `inventory` (rule 4).

**Provenance:** authored by the operator from roster row #9 and the
2026-08-29 red-team constraints (nonexistent subject; discovery probe as
anonymity guard); applied only via `evals/inject.sh` — run date recorded at
capture below.

**Ground truth (informal — the scored version is `gold.json`):**

- Failing resource: `inventory/rolebinding/inventory-reader-binding`
- Fault class: RBAC denial
- Mechanism: binding subject `inventory-synk` (nonexistent) vs actual SA
  `inventory-sync` → the worker's identity is bound to nothing → 403 on its
  ConfigMap reads → quiet stale serving (no crash, no events).
- Decisive evidence: worker logs pairing `discovery=ok` with `403
  Forbidden`; the subject/SA one-letter diff in captured RBAC JSON.
- Remediation: patch the binding subject (gold has the exact command);
  RoleBinding `subjects` are mutable (only `roleRef` is immutable).

**Design points (red-team constraints, encoded):**

- **Nonexistent subject**: `inventory-synk` names no SA, so re-pointing the
  pod's serviceAccountName is not a defensible alternative fix — the
  binding is the only object whose spec can change to grant the access.
- **Anonymity guard**: busybox wget with a mis-built Authorization header
  would authenticate as system:anonymous and ALSO get 403 — capture-time
  indistinguishable from the real fault, and unfixable by the gold
  remediation. The discovery probe (GET /api, granted to every
  authenticated subject via the system:discovery binding) turns "the token
  header works" into a log line the fixture carries; wait.sh requires it.
- **Naming**: Role/RoleBinding deliberately named `inventory-reader*` (not
  `configmap-reader*`) so the natural mechanism sentence never contains
  signature vocabulary of bad-config-ref (the class co-match trap the
  red-team flagged on mention-blind signatures).

**Wait condition:** worker logs contain BOTH `discovery=ok` and `403
Forbidden` (wait.sh; 5s poll, 300s cap; 10s sync cycle).

**Why deterministic:** RBAC evaluation is a pure lookup — an unbound
identity gets 403 on every request; discovery is granted to every
authenticated subject; the 10s cycle emits both log lines well inside the
wait budget. Only the node-cached busybox:1.36; TLS via
--no-check-certificate (busybox wget has no CA-bundle option).

**Rule 2 (one hop):** the sync loop lives in a ConfigMap-mounted script;
the pod spec shows only serviceAccountName, the downward-API env, and the
mount.

**Counterfactual-verification record (rule 6): 2026-08-29 ~07:40 IST** —
`inject.sh --no-capture` manifested both gates (`discovery=ok` AND `403
Forbidden` in the worker logs — the anonymity guard held: the token header
was proven sent before capture). Applied gold's remediation (patched the
binding subject to `inventory-sync`) → within one 10s cycle the logs showed
`sync ok: fetched inventory-sources` with no workload change. Wiped and
re-injected cleanly for the pristine capture.
