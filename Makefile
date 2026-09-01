# Reproduction entry points. `make eval` is the command in README's reproduction
# guide; everything here assumes only `uv` is installed (it provisions Python
# 3.12 and the locked dependencies itself).
.PHONY: help install verify test eval eval-baseline eval-solution eval-rules demo gate clean

help:
	@echo "install         uv sync — venv + locked dependencies"
	@echo "verify          re-derive every reported number from committed evidence (offline, free)"
	@echo "test            offline unit tests"
	@echo "gate            the full deterministic gate (tests, lint, types, secrets, evidence)"
	@echo "eval            run BOTH arms over the frozen 12-case set, 3 runs each (COSTS API TOKENS)"
	@echo "eval-baseline   run the frozen one-prompt baseline arm only"
	@echo "eval-solution   run the advanced solution arm only"
	@echo "eval-rules      run the rules-only ablation arm (FREE — no API calls)"
	@echo "demo            one case end to end, no scoring (cheapest live look at the agent)"

install:
	uv sync

# Free and offline: proves the numbers in README/CHANGELOG are what the frozen
# scorer says about the committed answer files. Start here.
verify:
	./evals/run.sh

test:
	uv run pytest -q

gate:
	bash scripts/checkpoints.sh

# Live runs. Needs ANTHROPIC_API_KEY in .env (see .env.example). Each arm is
# 12 cases x 3 replicate runs; measured cost is printed in the summary it writes
# to evals/results/<UTC-stamp>-<arm>/.
eval: eval-baseline eval-solution

eval-baseline:
	uv run python -m evals.run_eval --arm baseline --runs 3

eval-solution:
	uv run python -m evals.run_eval --arm solution --runs 3

# The ablation (design req 8). Deterministic and offline: costs nothing and is
# safe to re-run on any machine.
eval-rules:
	uv run python -m evals.run_eval --arm rules --runs 3

CASE ?= t2-rbac-sync-forbidden
demo:
	uv run python -m solution.agent --fixture evals/fixtures/$(CASE) --out .work/demo/$(CASE)
	@echo "--- report: .work/demo/$(CASE)/report.md"

clean:
	rm -rf .work/demo
