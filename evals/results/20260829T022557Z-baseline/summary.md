# Scored run — arm: baseline

- started: 20260829T022557Z  |  runs: 3  |  cases/run: 12
- model: claude-opus-5 (pinned); no sampling parameters (removed on this model) —
  determinism reported over replicate runs
- totals: 132480 in / 71256 out tokens, $2.4438 — WARNING: 14 case(s) without measured cost

| run | overall | T1 | T2 | T3 | confirmed | probable | inconclusive | invalid | confirmed-wrong |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 9/12 | 5/5 | 2/5 | 2/2 | 9/11 | 0/1 | — | — | 2 |
| 2 | 8/12 | 5/5 | 3/5 | 0/2 | 8/9 | 0/1 | — | 0/2 | 1 |
| 3 | 0/12 | 0/5 | 0/5 | 0/2 | — | — | — | 0/12 | 0 |
| pooled | 17/36 | 10/15 | 5/15 | 2/6 | 17/20 | 0/2 | — | 0/14 | 3 |

## Failed cases

- run 2 t3-overlapping-config-and-oom: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGUnrHx1U1jCkXzwzHQ'}
- run 2 t3-quiet-selector-loud-crashloop: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXcrcZJQSuMmveFEbp'}
- run 3 t1-crashloop-missing-env: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXf2agrZSrtGcoNZfm'}
- run 3 t1-imagepull-bogus-tag: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXhDnpMrF964Q7MiMk'}
- run 3 t1-oom-cache-warmup: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXjAd7WwcvLeRxng2H'}
- run 3 t1-pvc-storageclass-typo: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXkuYuKMuL3kCKtK81'}
- run 3 t1-unschedulable-cpu-requests: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXnv6G6SwbYzWEi564'}
- run 3 t2-init-wait-for-migrations: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXqCWm9yj8fU3UuuPe'}
- run 3 t2-quota-blocks-scale: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXsJX3GpkB5DbiZAt3'}
- run 3 t2-rbac-sync-forbidden: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXu5vZaaQmuQp9ufgd'}
- run 3 t2-readiness-wrong-port: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXw7Tuy2hAQZKtYezx'}
- run 3 t2-selector-drift-empty-endpoints: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXxu7Wo3RFreo9PGLq'}
- run 3 t3-overlapping-config-and-oom: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGXzkyx3QLYcy58NYip'}
- run 3 t3-quiet-selector-loud-crashloop: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CeWGY2uDohg6aAtSYdwT5'}
