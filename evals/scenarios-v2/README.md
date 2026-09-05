# Scenario root v2 — additive cases above the frozen set

The frozen 12-case set lives in `evals/scenarios/` and keeps its identity and
its count (tag `case-set-freeze`). Cases here are **additive**: they run through
the same `inject.sh` / `capture.sh` / `run_eval.py` pipeline by pointing at this
root (`inject.sh --root evals/scenarios-v2`, `run_eval --scenarios-root
evals/scenarios-v2`), their fixtures land in the single `evals/fixtures/` tree
so every gate scan reaches them, and nothing here moves a frozen number.

The authoring contract in [`../scenarios/README.md`](../scenarios/README.md)
applies in full, with these deltas:

| Delta | Rule here |
|---|---|
| **Scorer** | Scored by `evals/scoring_v2.py`, the frozen rubric re-keyed by value plus one class (`webhook-admission-block`); `tests/test_scoring_v2.py` pins parity on every frozen phrasing. `run_eval.py` selects it by root and records `scorer` in `summary.json`. |
| **Cluster-scoped gold** | A cluster-scoped `failing_resource` writes `"namespace": ""` in `gold.json`. The v2 scorer treats every "no namespace" spelling an arm may produce (`""`, `-`, `none`, `cluster-scoped`, `n/a`, …) as equal for a cluster-scoped kind; a namespaced kind keeps its namespace as identity. |
| **Admission webhook configurations** | The one cluster-scoped kind a scenario may create (rule 4 otherwise unchanged: ClusterRole, PV, StorageClass stay refused). Every such document carries `incident-lab.dev/scenario: <id>` and intercepts exactly `operations: ["CREATE"]` on `resources: ["pods"]` (flow style, so the lint can read it); `inject.sh` refuses the scenario otherwise, deletes labelled configurations first on reset, and refuses to run past an unlabelled one. `capture.sh` records the kinds since capture schema 2 and scrubs the pipeline label out of the fixture (`checkpoints.sh` gates on it). A cluster-scoped gold object's `metadata.name` must share no token with anything the API server's error message prints, so no arm can name it without reading it. |
| **Noise pack** | `_noise/` here is the frozen pack's 20 healthy namespaces verbatim plus three broken-but-irrelevant decoys (`generate.sh`); `inject.sh` uses the root's own pack for `t3-*` ids. |
| **Case ids** | May be symptom-named (`t2-checkout-release-stalled`) rather than mechanism-named: the id reaches the agent in its first message, and a case built around an invisible cause must not hand the mechanism over in its name. |
| **Two-phase scenarios** | `setup.yaml` (pre-fault healthy state, waited Available) then `fault.yaml`; `wait.sh` gates on the decisive evidence and treats a half-manifested fault as a distinct failure to re-inject, never something to wait out. |

## Roster

| id | tier | gold class | what it adds | status |
|---|---|---|---|---|
| `t2-crossns-externalname-selector` | T2 | service-selector-mismatch | cause one namespace away from the page, reached through an ExternalName alias | captured 2026-09-02; scored (CHANGELOG [13]) |
| `t3-crossns-decoys` | T3 | service-selector-mismatch | the same objects under the decoy noise pack — the measured control for "hard because noisy" | captured 2026-09-04; scored (CHANGELOG [14]) |
| `t2-checkout-release-stalled` | T2 | webhook-admission-block | cause is a cluster-scoped orphaned `ValidatingWebhookConfiguration`, outside every arm's reach today | captured 2026-09-05; scored (CHANGELOG [15], `docs/experiments/2026-09-04-webhook-outage.md`) |

Claims from these cases follow the pre-registration method in
`docs/experiments/`: per-arm predictions written before the run, one scoring
invocation, results read from `rows.jsonl` sub-scores before any pooled number
is quoted.
