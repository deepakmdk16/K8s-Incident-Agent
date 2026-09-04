# CLAUDE.md — K8s Incident Agent

Durable engineering guide for this project. Merge with the global
`~/.claude/CLAUDE.md`; where this file is silent, the global rules apply. It
loads every session — keep it lean.

## Invariants (do not violate)

- **Baseline and advanced are both mandatory.** `baseline/` stays simple and
  frozen once it works; `solution/` must show *meaningful* improvement
  (capability, reliability, efficiency, coverage, or engineering quality —
  never cosmetic). `ablation/` is the rules-only control arm that anchors both.
- **Gates are fixed; code bends.** Never weaken a linter/test/eval config,
  skip/delete a failing test, or loosen an acceptance threshold to get green —
  fix the code. A gate change is legitimate only when stated explicitly in the
  commit message with the reason.
- **Every claim connects to evidence in the repo.** No number in README or
  CHANGELOG that a reader cannot reproduce from the tree.
- **A score gap names a difference, never its cause.** Before attributing an
  arm-to-arm gap to capability, decompose it into the per-row sub-scores the
  harness already records (`resource_correct`, `class_correct`,
  `matched_classes` in `rows.jsonl`). A pooled number can move for reasons the
  rubric's vocabulary controls rather than the agent's behaviour — twice now
  (docs/failure-modes.md, 2026-08-29 and 2026-09-04).
- **Report outcomes as measured — quote the output.** A test that did not
  demonstrably pass is inconclusive, never a pass. Never claim "done" on
  unverified code, and never report a gate as green from a summary line, a
  prior run, or inference: run it in this session and quote its actual output.
  If it was not run, say so plainly. (This is the top measured failure mode
  across prior sessions: work declared done from summary output, real checks
  only run when someone pushed back.)
- **Diagnose before blaming the environment.** Do not attribute a failure to
  infrastructure, the network, an API, or "flakiness" until it has been
  reproduced at least twice and artifact-side causes are ruled out (build
  errors, missing files, permissions, wrong path, stale config). State the
  evidence for an external verdict explicitly. Retry hardening added on a
  guess is wasted work that hides the real bug.
- **Frozen eval, pre-registered claims.** Capability claims are made against
  the frozen case set, with the metric fixed before the run. Changing the
  case set or the primary metric happens only via an explicit, disclosed
  update to the decision doc (see Decisions) — never silently.
- **Secrets from env only** — `.env` (gitignored), keys never committed, never
  in logs or committed artifacts.
- **CHANGELOG.md is the improvement narrative.** One entry per meaningful
  iteration, written at the time it happens (not reconstructed later), each
  linked to the evidence that drove the next decision.
- **Never import another engagement's artifacts.** Generalized practice —
  write tests, verify before claiming, gate mechanically — transfers freely;
  artifacts and their specifics (prompts, scripts, review criteria, formats)
  do not.

## Workflow

- **Goal-driven slices**: turn each task into a verifiable goal with a check
  ("add X" → "failing test for X, then pass it"). One branch per slice → PR →
  merge; never work on `main` directly.
- **Gates**: `bash scripts/preflight.sh` once per session (env, keys, tools);
  `bash scripts/checkpoints.sh` before every commit — it is the loop's exit
  condition. Git hooks (`githooks/`, wired via `git config core.hooksPath
  githooks`) enforce them: pre-commit = secret/privacy scan + no direct
  commits to main (`ALLOW_MAIN=1` overrides); pre-push = full checkpoints.
- **Self-improving loop (standing rule — act on it without being asked).**
  Every issue that gets diagnosed and fixed ships its *prevention* in the same
  commit, chosen in this order: (1) a test or checkpoint gate if the mistake
  is machine-checkable; (2) a rule added to this file if it changes how we
  always work; (3) an entry in [docs/failure-modes.md](docs/failure-modes.md)
  (what happened → prevention now in place) if situational. A solved issue
  recurring is itself a failure mode — log it and escalate the prevention a
  level.
- **STATUS.md is operator-local and open-items-only.** It is gitignored;
  update it whenever an item opens or closes. Closing an item means
  **deleting** it — never leaving it as `[x]` (checkpoints.sh fails on
  `- [x]` when the file exists). History is `git log`; CHANGELOG.md is the
  improvement narrative. Durable facts a cold session needs (running
  clusters, remotes, frozen decisions) live in a short "standing state"
  paragraph, not as completed items. Before ending a session, bring STATUS.md
  to exactly the current open items — a cold session must be able to take its
  work list from STATUS.md alone.

## Decisions

[docs/decisions/problem-selection.md](docs/decisions/problem-selection.md)
binds the eval design: the primary metric is root-cause identification rate on
the frozen fault-injected case set, reported per difficulty tier. Changing the
problem, the metric, or the frozen case set requires an explicit decision-doc
update naming the condition that fired — never a silent edit.

## Code standards

Enforced by the toolchain (linter/formatter/type-checker config run by the
checkpoints gate), not by good intentions.

- **Types everywhere.** Full annotations on every function signature (hints +
  a type checker). Types are how a later session — and the agent — discovers
  what already exists.
- **Search before writing.** Grep for an existing function/component before
  adding one. Rebuilding what already exists is a known coding-agent failure
  mode (context lost between sessions), not a style preference.
- **Small units.** One job per function; ~40 lines is the review trigger, not
  a hard limit. A function needing a section comment wants to be two functions.
- **One logger, structured.** A single logging module; every line carries a
  run/correlation ID so one execution traces end to end. No bare prints in
  `baseline/` or `solution/`.
- **Tests alongside code.** Every acceptance-test path has a test; every bug
  fixed ships its regression test (see the self-improving loop). No
  coverage-percentage target — a percentage invites gaming.
- **Errors surface.** No catch/except that swallows, no default value that
  masks a failure. A silent failure inside an agent loop is undebuggable at
  3am.

## Structure

```
baseline/    the simple, frozen reference solution
solution/    the advanced solution
ablation/    rules-only control arm (no LLM), a third comparison arm
common/      shared kernel all arms import (structured logging, …)
tests/       offline, deterministic; no network, no live LLM calls in unit tests
evals/       the frozen case set: scenarios, scoring, results
docs/        decisions, experiments, failure modes, design notes
scripts/     preflight.sh, checkpoints.sh (deterministic gates)
.work/       disposable scratch (gitignored)
```

## Toolchain (Python 3.12 + uv)

Chosen for agent-harness iteration speed and clean-clone simplicity (`uv sync`
is the entire install). Versions: `.python-version` pins 3.12, `uv.lock` pins
deps.

- `uv sync` — venv + all dependencies (dev group included)
- `uv run pytest -q` — tests (offline, deterministic)
- `uv run ruff check .` / `uv run ruff format --check .` — lint / format gate
  (`uv run ruff format .` rewrites in place — never runs inside a gate)
- `uv run pyright` — type check, **strict mode**, whole tree

The four checks (pytest, ruff check, format --check, pyright) run inside
`scripts/checkpoints.sh` (python gate) and therefore in the pre-push hook; the
post-edit hook ruff-checks each edited `.py` under baseline/ solution/ common/
tests/ evals/ immediately.
