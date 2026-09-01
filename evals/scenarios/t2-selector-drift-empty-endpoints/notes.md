# t2-selector-drift-empty-endpoints — scenario notes

**Tier:** T2 (symptom far from cause: gateway timeouts page, fault is a
one-word drift on a Service object that itself emits zero events).
**Status:** authored 2026-08-29 (Sat morning); captured and scored in the
frozen case set (fixtures: `evals/fixtures/t2-selector-drift-empty-endpoints/`).

**Namespaces owned:** `shop` (rule 4).

**Provenance:** authored by the operator from roster row #6; applied only
via `evals/inject.sh` — run date recorded at capture below.

**Ground truth (informal — the scored version is `gold.json`):**

- Failing resource: `shop/service/catalog`
- Fault class: service selector mismatch
- Mechanism: Service selector `app=catalog-api` vs pod labels `app=catalog`
  → Endpoints empty → gateway connections refused, while catalog pods are
  Ready with passing probes (quiet: no events anywhere point at the
  Service).
- Decisive evidence: empty Endpoints against Ready pods + the selector/label
  diff visible in the captured Service and Pod JSON + gateway failure logs.
- Remediation: patch the Service selector; no workload change (gold has the
  exact command).

**Wait condition:** Endpoints empty AND both catalog pods Ready AND a
gateway `catalog fetch FAILED` log line (wait.sh; 5s poll, 300s cap).

**Why deterministic:** label selection is a pure predicate — the Endpoints
controller can never populate a selector that matches nothing; the gateway
poller fails on every 5s cycle (kube-proxy refuses connections to a service
with no endpoints); readiness probes against busybox httpd on the correct
port always pass. Only the node-cached busybox:1.36 is used.

**Rule 2 (one hop):** both app behaviors live in ConfigMap-mounted scripts
(`catalog-scripts/run.sh`, `storefront-gateway-scripts/run.sh`); pod specs
show only the wiring.

**Gold-side asymmetry:** relabeling the pods to `app=catalog-api` would also
reconnect the service, but the Deployment's selector/labels are consistent
with each other and with the deployment's own name — the Service's selector
is the odd value out and is the only single-object change; gold = the
Service. (Deployment selectors are also immutable, so the relabel path would
require a recreate — another reason the Service patch is the defensible fix.)

**Counterfactual-verification record (rule 6): 2026-08-29 ~07:30 IST** —
`inject.sh --no-capture` manifested all three gates (Endpoints empty;
catalog pods Ready x2; gateway `catalog fetch FAILED`). Applied gold's
remediation (patched the Service selector to `app: catalog`) → Endpoints
populated with both pod IPs within seconds and the gateway logs flipped to
`catalog fetch ok` with no workload change. Wiped and re-injected cleanly
for the pristine capture.
