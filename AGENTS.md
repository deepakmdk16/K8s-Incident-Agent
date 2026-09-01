# AGENTS.md

Instructions for any coding agent working in this repository.

**[CLAUDE.md](CLAUDE.md) is the single source of truth. Read it first and follow
it in full.** This file exists so agents and reviewers that look for the
conventional filename find the instructions; it deliberately does not restate
them, because two instruction files that drift apart are worse than one.

## Orientation

This is a Kubernetes incident-diagnosis agent: an on-call engineer's
hypothesis→evidence→verify loop over kubectl-shaped tools, producing a
root-cause report with a cited evidence chain; a human approves any
remediation.

`baseline/` holds the simple reference solution (frozen once working);
`solution/` holds the advanced one. Both are required. `ablation/` is a
rules-only control arm used to measure what a non-LLM diagnoser can reach.
`evals/` verifies all three against the frozen case set, and `CHANGELOG.md` is
the evidence-linked improvement narrative.

## Non-negotiables

- `bash scripts/checkpoints.sh` must pass before every commit — it is the
  definition of done, not a formality. Never report a gate as green without
  running it and quoting its output.
- Never weaken a test, linter, threshold, or gate to make it pass. Fix the code.
- Secrets come from `.env` (gitignored) only. No credentials or personal data
  in any committed file.
- Every claim in README or CHANGELOG must link to evidence reproducible from
  the repo.
- Never import another engagement's artifacts; generalized practice transfers,
  artifacts do not.

Full rules, invariants, code standards, and workflow: **[CLAUDE.md](CLAUDE.md)**.
