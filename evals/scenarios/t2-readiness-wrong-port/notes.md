# t2-readiness-wrong-port — scenario notes

**Tier:** T2 (symptom at the gateway, fault one port number deep in the
backend's probe). **Status:** authored 2026-08-29 (Sat morning); captured
and scored in the frozen case set (fixtures:
`evals/fixtures/t2-readiness-wrong-port/`).

**Predicted baseline-solvable** (roster tier-honesty note): the not-Ready
pods put their describes — including the 'Readiness probe failed' events —
into the baseline's own curation dump, so this case calibrates fairness and
golds the readiness atom; the T2 measured-improvement gap rests on #6/#8/#9.

**Namespaces owned:** `orders` (rule 4; also used by t3-overlapping at ITS
capture — each capture starts from a wiped cluster, so there is no overlap).

**Provenance:** authored by the operator from roster row #7; applied only
via `evals/inject.sh` — run date recorded at capture below.

**Ground truth (informal — the scored version is `gold.json`):**

- Failing resource: `orders/deployment/orders-api`
- Fault class: failing readiness probe (wrong port)
- Mechanism: probe targets :8081, app serves :8080 → probes fail every
  cycle → Running-never-Ready → Endpoints empty → gateway refused.
- Decisive evidence: Unhealthy events naming :8081 against a spec/script
  serving :8080; Running/NotReady pods; empty Endpoints.
- Remediation: patch the probe port to 8080 (gold has the exact command).

**Wait condition:** both pods Running/NotReady + Unhealthy event naming
8081 + Endpoints empty (wait.sh; 5s poll, 300s cap).

**Why deterministic:** nothing listens on 8081, so the probe outcome is a
connection refused on every cycle — no timing, no load sensitivity; busybox
httpd on 8080 always serves the probe's counterfactual. Only the node-cached
busybox:1.36.

**Rule 2 (one hop):** app and gateway logic in ConfigMap-mounted scripts;
the probe misdirection lives in the pod spec where a probe belongs.

**Gold-side asymmetry:** none — the probe port is the only value out of
agreement with both the container port and the serving script; changing the
app to serve 8081 would contradict two other spec values.

**Counterfactual-verification record (rule 6): 2026-08-29 ~07:35 IST** —
`inject.sh --no-capture` manifested all three gates (Running/NotReady x2;
`Readiness probe failed: ... :8081 ... connection refused` event; Endpoints
empty). Applied gold's remediation (patched the probe port to 8080) →
`deployment "orders-api" successfully rolled out`, Endpoints populated with
both pod IPs, and the gateway log flipped `orders fetch FAILED` →
`orders fetch ok` across consecutive lines. Wiped and re-injected cleanly
for the pristine capture.
