# t3-quiet-selector-loud-crashloop — scenario notes

**Tier:** T3 — **the required hard case** (decision doc: red-herring
composition + cluster noise). **Status:** authored 2026-08-29 (Sat
morning); captured and scored in the frozen case set (fixtures:
`evals/fixtures/t3-quiet-selector-loud-crashloop/`).

**Namespaces owned:** `search`, `analytics-batch` (rule 4; neither collides
with the noise pack's 20 namespaces — checked against
`evals/scenarios/_noise/namespaces.txt`).

**Provenance:** authored by the operator from roster row #11; applied only
via `evals/inject.sh` (t3-* id → noise pack applied first) — run date
recorded at capture below.

**Ground truth (informal — the scored version is `gold.json`):**

- Failing resource: `search/service/search`
- Fault class: service selector mismatch (the QUIET fault: zero events, all
  pods Ready)
- Mechanism: Service selector `app=search-api` vs pod labels `app=search` →
  Endpoints empty → gateway refused.
- Decoy: `analytics-batch/report-generator` crashloops LOUDLY — genuine
  conditional fault (missing EXPORT_BUCKET env; rule 1 honored: setting the
  env makes it run), maximally visible in events/restarts, verifiably off
  the search path (different namespace; nothing in `search` references it;
  its own logs say it is a nightly report exporter).
- Remediation: patch the Service selector (gold has the exact command).

**Adversarial design:** the baseline's own curation rule (describes of
not-Ready pods + crashing logs) mechanically steers a shallow investigation
at the decoy — the only not-Ready pod in the cluster is the decoy, while
the actually-paged path shows healthy pods and an empty Endpoints object
that only a targeted service-side query surfaces. A disciplined
investigation answers the page (search path) and rules the decoy out in the
ledger. ~20 noise namespaces raise the search cost for both arms.

**Wait condition:** Endpoints empty + search pods Ready x2 + gateway FAILED
log + decoy restarts >= 3 with CrashLoopBackOff (wait.sh; 5s poll, 300s
cap; decoy backoff reaches 3 restarts in ~90s).

**Why deterministic:** selector matching and env-var presence are pure
predicates; the decoy's conditional exit happens identically on every start
and kubelet backoff yields >= 3 restarts well inside the wait budget; the
gateway poller fails every 5s cycle against an endpoint-less service. Only
node-cached busybox:1.36 anywhere (noise pack included).

**Rule 2 (one hop):** all four behaviors (search httpd, gateway poller,
decoy exporter, noise workloads) live in ConfigMap-mounted scripts.

**Gold-side asymmetry:** as in t2-selector-drift: nothing in the namespace
is named or labeled `search-api` — the Deployment is named `search` with
labels `app=search` agreeing with its own selector, so the Service's
selector value dangles against the whole namespace and is the single odd
value (Deployment selectors are also immutable) → gold = the Service.
(Adversarial verify 2026-08-29 caught the original authoring naming the
Deployment `search-api`, which made the drift defensibly two-sided on the
hard case; renamed before capture, rehearsal unaffected — same objects
modulo the name.)

**Counterfactual-verification record (rule 6): 2026-08-29 ~07:50 IST** —
`inject.sh --no-capture` manifested all gates (Endpoints empty; search pods
Ready x2; gateway `search fetch FAILED`; decoy restarts=4 CrashLoopBackOff;
noise pack Available first). Applied gold's remediation (patched the
Service selector to `app: search`) → Endpoints populated with both pod IPs
and the gateway logged `search fetch ok` WHILE the decoy was still failing
(restarts=5 at confirmation) — the primary fix alone answers the page.
Decoy counterfactual: `set env EXPORT_BUCKET=s3://acme-nightly-reports` →
rolled out, logs `idle until next export window`. Wiped and re-injected
cleanly for the pristine capture.
