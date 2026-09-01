#!/usr/bin/env bash
# The evidence gate: re-derive every reported number from the committed bundles.
#
# Deterministic and OFFLINE by construction — it makes no API calls and touches
# no cluster. checkpoints.sh runs this on every commit and push, so it must stay
# that way; a live run here would bill the operator per commit.
#
# What it proves: the pooled scores, per-tier scores, calibration counts and
# resource-identification rates are re-derived here from the committed
# answer.json files with the frozen scorer, and the solution arm's declared bar
# (evals/reported.json: pooled/resource >=36, confirmed-wrong <=0, beats both
# other arms, rules failing on >=3 cases) is asserted — an edited or degraded
# solution bundle fails here. The rules/baseline cells are re-derived and
# printed, not independently asserted against README.md/CHANGELOG.md; compare
# those by eye.
#
# To run the arms for real (costs API tokens), use `make eval`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec uv run python -m evals.verify_reported
