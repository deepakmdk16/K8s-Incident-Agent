# t2-crossns-externalname-selector

**Tier:** T2. **Status:** captured 2026-09-02 (`evals/inject.sh --id
t2-crossns-externalname-selector --root evals/scenarios-v2`), authoring cluster
`kind-incident-lab`, kind node image pinned to the same digest as the frozen
12-case set (server v1.37.0).

**Set:** `evals/scenarios-v2/` — the additive root. The frozen 12-case set under
`evals/scenarios/` keeps its identity and its count; `run_eval.discover_cases`
still defaults to it.

## Informal ground truth

`storefront/checkout-api` will not accept traffic unless the payment gateway
answers. It calls the gateway at `http://payments-gateway:8080/health`, which in
its own namespace is an **ExternalName** alias pointing at
`payments-gateway.payments.svc.cluster.local`. That alias is correct.

The defect is one namespace away: `payments/payments-gateway` selects
`app=payments-gateway`, while the pods behind it carry
`app=payments-gateway-api`. Its Endpoints object therefore never populates, the
checkout readiness gate fails on every attempt, and the page fires against
`storefront` while the object whose spec must change lives in `payments`.

## Why this case exists

Cross-namespace attribution. Every case in the frozen set has its cause and its
symptom in one namespace, so nothing there can distinguish an agent that
follows a reference across a boundary from one that searches the paged
namespace exhaustively. It is also the case that exercises the V2 admissibility
rule in the direction that matters: the page alone licenses only `storefront`,
and `payments` becomes citable **only** once the agent has read the ExternalName
Service and quoted its target. Verified directly against the captured fixture:

    admissible from page       : ['', 'storefront']
    after the ExternalName read: ['', 'payments', 'storefront']

## Wait condition

`wait.sh` gates on all three at once: Endpoints `payments/payments-gateway` has
no addresses, **and** both `payments-gateway-api` pods are Ready, **and**
`storefront/checkout-api` has logged `payment gateway UNREACHABLE`. The middle
condition is the one that makes the evidence decisive rather than coarse — it
rules out "the gateway workload is down", which would present the same way from
inside storefront.

## Gold-side asymmetry

Two objects could in principle change: the Service in `payments`, or the
ExternalName alias in `storefront`. The fixture makes the payments Service
canonical — the alias resolves to exactly the Service that exists, by name and
namespace, and the gateway pods behind it are Ready and serving. Nothing about
the alias is wrong, so editing it could only route around the fault. Gold is
`service/payments-gateway` in `payments`.

The designed distractor is `checkout-api`'s own readiness probe, which is
genuinely failing. It is a symptom: the probe is correct and passes the moment
the upstream answers. An arm that names it fixes nothing.

## Determinism

Single-node kind, kindnet, no NetworkPolicy involved. Both images are the
node-cached `busybox:1.36` (imported into containerd before injection), so no
pull can throttle. Label selection and Endpoints population are synchronous
control-plane behaviour with no timing race; the only wait is for the two
gateway pods to pass their readiness probes, which `wait.sh` gates on
explicitly.

## Counterfactual verification (authoring contract rule 6)

Rehearsed live on 2026-09-02 **before** the pristine capture, on a separate
`--no-capture` injection:

| step | observed |
|---|---|
| fault live | `checkout-api` 0/1 Ready x2; `Endpoints payments/payments-gateway <none>` |
| applied `gold.json`'s `remediation_summary` verbatim | `kubectl -n payments patch service payments-gateway -p '{"spec":{"selector":{"app":"payments-gateway-api"}}}'` → `service/payments-gateway patched` |
| recovery | `Endpoints payments/payments-gateway 10.244.0.7:8080,10.244.0.8:8080`; both `checkout-api` pods `1/1 Running`; logs turned over to `payment gateway reachable - accepting checkout submissions` |

No object in `storefront` was touched, which is what `remediation_summary`
claims. The cluster was then wiped and re-injected clean for the capture that
ships here.

## Namespaces owned

`storefront`, `payments`.
