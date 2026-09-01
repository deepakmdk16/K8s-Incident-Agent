# Baseline: the one-prompt diagnoser

The simple reference arm. **Frozen once scored** (bugfixes only, no features) so
measured improvement compares against a fair, stable floor. Same inputs, same
output contract, same scorer as `solution/` — the arms differ only in how they
investigate.

## What it is

One LLM call per case (`claude-opus-5`, no tools, no retries, no follow-ups)
over a curated paste of cluster state. This models the honest human baseline:
a rushed on-call engineer pasting what they grabbed into a chat window and
asking "what's wrong?".

## The dump-curation policy (documented, frozen with this arm)

Implemented in [curate.py](curate.py); exactly this, nothing else:

1. **The page** (`page.txt`) — the symptom the on-call was paged with.
2. **`kubectl get all -A`** — verbatim.
3. **`kubectl describe`** of every not-ready resource. Not-ready means: pods
   with `READY a/b, a<b` or status outside Running/Completed/Succeeded;
   workloads with a `READY a/b` column where `a<b`; replicasets/daemonsets
   with `READY < DESIRED`.
4. **Last 50 log lines** of every not-ready pod, both channels (current and
   `--previous`).

Deliberately excluded (a rushed human doesn't paste these): events, full
manifests, endpoints/quota/RBAC state, anything from namespaces that look
healthy in `get all`. The expected consequence — stated before any scored run —
is that this baseline does well when the decisive evidence is in a describe or
a crash log (T1), and degrades when the cause is far from the symptom (T2/T3).

Per-case resource usage (exact API token counts, cost, duration) is recorded in
`metrics.json` by the harness; the prompt itself is saved as `prompt.txt`, so
the baseline-vs-solution resource difference is disclosed from measurement.

## Run

```bash
uv run python -m baseline.diagnose --fixture evals/fixtures/<case> --out <dir>
# scored, all cases, 3 replicate runs:
uv run python -m evals.run_eval --arm baseline
```

Requires `ANTHROPIC_API_KEY` (from `.env` or the environment). If the key is
identity-linked and not scoped to one workspace ("All workspaces" in the
Console), also set `ANTHROPIC_WORKSPACE_ID=wrkspc_...` — such keys are rejected
with a 400 without it.
