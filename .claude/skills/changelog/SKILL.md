---
name: changelog
description: Append the next numbered Improvement Changelog entry at the time it happens, linked to concrete evidence (eval output, failing test). Use after every meaningful iteration — CHANGELOG.md is the project's measured-improvement narrative.
---

# Changelog entry

1. Next N = highest `## [N]` in CHANGELOG.md plus one.
2. Append using the template at the top of CHANGELOG.md:
   - **Change** — what changed in the solution or agent design.
   - **Why** — the observation/evidence that motivated it, as a link.
   - **Evidence after** — measurement showing the effect, as a link. Numbers
     over adjectives.
   - **Next decision it drove.**
3. Every link must point at a committed file (`evals/results/`, `evals/out/`,
   test output committed to the repo) — a reader must be able to open it. A
   claim without committed evidence violates a repo invariant.
4. If the entry opens or closes work, update STATUS.md in the same commit.

Refuse to write an entry that has no evidence link — park the iteration until
the evidence exists (run the eval) rather than reconstructing it later.
