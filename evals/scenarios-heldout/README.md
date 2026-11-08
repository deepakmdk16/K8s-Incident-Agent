# Scenario root heldout — the held-out set (STATUS 3.2)

Cases nobody tuned on. The frozen 12-case set (`evals/scenarios/`) is
saturated and every case since was built in the same shop/payments/orders
world; this root asks whether an arm's score transfers to a company it has
never seen, and puts the cause outside the served object universe on two more
kinds. Design, roster and the per-arm predictions fixed before any capture:
[`docs/experiments/2026-11-07-heldout-set.md`](../../docs/experiments/2026-11-07-heldout-set.md).

The cases are **additive** and run through the unchanged pipeline by pointing
at this root (`inject.sh --root evals/scenarios-heldout`, `run_eval
--scenarios-root evals/scenarios-heldout`); fixtures land in the single
`evals/fixtures/` tree. The authoring contract in
[`../scenarios/README.md`](../scenarios/README.md) and every delta in
[`../scenarios-v2/README.md`](../scenarios-v2/README.md) apply in full (v2
scorer, cluster-scoped gold writes `"namespace": ""`, symptom-named ids,
two-phase scenarios, a noise pack per root), with these further deltas:

| Delta | Rule here |
|---|---|
| **Renamed vocabulary** | A freight-logistics / fleet-telematics company. No namespace, workload, container, Service, ConfigMap, Secret or environment-variable name here appears in the frozen 12, the v2 cases or either noise pack. Pages keep the contractual header line (the rules arm parses the namespace from it) and change everything else: alert-source style, symptom prose, the standing ask. |
| **Scored once, never tuned against** | Each arm is scored on this root once, after the set is committed; nothing under `solution/`, `baseline/` or `ablation/` changes on its evidence. The rules arm (free, deterministic) runs in the authoring slice; the LLM arms are deferred per the STATUS scoring policy, with their predictions already fixed in the design doc. |
| **CustomResourceDefinitions** | The second cluster-scoped kind a scenario may create (after admission webhook configurations). Every CRD document carries `incident-lab.dev/scenario: <id>`; `inject.sh` refuses the scenario otherwise, deletes labelled CRDs by that label on reset (before the namespace wipe), and refuses to run past an unlabelled one. A CRD belongs in `setup.yaml`; after applying it, `inject.sh` runs `kubectl wait --for=condition=Established --timeout=60s` on every scenario-labelled CRD and dies on timeout, so the CRD is Established before `fault.yaml` creates a custom resource of it (added 2026-11-08, after the captures; untested live). |
| **Capture schema 3** | `capture.sh` records `cluster/customresourcedefinitions.json`, a describe per CRD, and the custom resources of every namespaced CRD under `ns/<ns>/crs/<plural>.<group>.json` with their describes; `checkpoints.sh` gates on the CRD roster for a schema-3 fixture. No reader serves these kinds: the capture is ahead of the tools, as schema 2 was before the webhook kinds were served. |
| **Second new class** | `pod-security-violation` (Pod Security admission refusing a pod for its namespace's enforce level; the failing resource is the Namespace) joins `webhook-admission-block` in `evals/scoring_v2.py`; its signature tests live in `tests/test_scoring_v2.py`. |
| **Noise pack** | `_noise/generate.sh` emits twenty new healthy namespaces and two broken-but-irrelevant decoys, all renamed; `inject.sh` uses it for `t3-*` ids under this root. |

### Capture — known limitation and its control

`capture.sh`'s `cap_json` chooses the Secret scrub — every `.data` and
`.stringData` value redacted on every item, whatever its per-item kind — by
the kind named in its own arguments (the `secrets` call), and the general scrub
redacts `.data` only on items whose `kind` is `Secret`. A custom resource that
embeds credentials in its own fields (a `spec.apiToken`, say) is therefore
captured by the general scrub with those fields intact. No case in this root
does that (`RouteProfile` carries a solver endpoint and tuning parameters), and
the independent gate in `scripts/checkpoints.sh` — `fixtures free of k8s
credential material` (service-account JWTs, base64 PEM, kubeconfig data
embeds, token values) — is the control that catches one that does, on every
run. Authoring rule until the scrub learns kinds from the CRD roster: a custom
resource in a scenario carries no credential-shaped value. Disclosed in the
PR #9 review (2026-11-08); no code change.

## Roster

| id | tier | gold class | gold resource | universe | status |
|---|---|---|---|---|---|
| `t1-reefer-telemetry-offline` | T1 | pod-unschedulable | deployment `cold-chain/reefer-monitor` | inside | captured 2026-11-08 |
| `t2-route-planning-degraded` | T2 | app-crashloop | routeprofile `dispatch/weekday-routing` (CRD-defined kind) | **outside** — no tool knows the kind | captured 2026-11-08 |
| `t2-customs-filing-timeouts` | T2 | service-selector-mismatch | service `customs-docs/manifest-renderer` | inside | captured 2026-11-08 |
| `t2-yard-gate-release-blocked` | T2 (two-phase) | pod-security-violation | namespace `yard-ops` (cluster-scoped) | **outside** — captured, served by no tool | captured 2026-11-08 |
| `t3-driver-login-failures` | T3 (noise pack) | rbac-denial | rolebinding `driver-portal/driver-auth-oidc-reader` | inside | captured 2026-11-08 |

Status moves to `captured <date>` when the fixture lands, and to `scored
(rules)` / `scored (all arms)` with the bundle reference as each arm runs.
Claims from these cases follow the pre-registration method in
`docs/experiments/`: predictions written before the run, one scoring
invocation per arm, results read from `rows.jsonl` sub-scores before any
pooled number is quoted.
