# Scored run — arm: baseline

- started: 20260828T184246Z  |  runs: 3  |  cases/run: 1
- model: claude-opus-5 (pinned); no sampling parameters (removed on this model) —
  determinism reported over replicate runs
- totals: 0 in / 0 out tokens, $0.0000 — WARNING: 3 case(s) without measured cost

| run | overall | T1 | confirmed | probable | inconclusive | invalid | confirmed-wrong |
|---|---|---|---|---|---|---|---|
| 1 | 0/1 | 0/1 | — | — | — | 0/1 | 0 |
| 2 | 0/1 | 0/1 | — | — | — | 0/1 | 0 |
| 3 | 0/1 | 0/1 | — | — | — | 0/1 | 0 |
| pooled | 0/3 | 0/3 | — | — | — | 0/3 | 0 |

## Failed cases

- run 1 t1-crashloop-missing-env: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'anthropic-workspace-id is required when authenticating with an identity-linked API key; send the id of the workspace this request acts in.'}, 'request_id': None}
- run 2 t1-crashloop-missing-env: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'anthropic-workspace-id is required when authenticating with an identity-linked API key; send the id of the workspace this request acts in.'}, 'request_id': None}
- run 3 t1-crashloop-missing-env: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'anthropic-workspace-id is required when authenticating with an identity-linked API key; send the id of the workspace this request acts in.'}, 'request_id': None}
