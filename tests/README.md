# Tests

Offline and deterministic: no network, no live LLM calls in unit tests (mock the
model boundary). LLM-touching checks live in `evals/` where cost and tokens are
recorded. The suite runs through `scripts/checkpoints.sh` alongside the lint,
format, and type gates.
