# Scored run — arm: baseline

- started: 20260829T043820Z  |  runs: 3  |  cases/run: 12
- model: claude-opus-5 (pinned); no sampling parameters (removed on this model) —
  determinism reported over replicate runs
- totals: 132480 in / 72228 out tokens, $2.4681 — WARNING: 14 case(s) without measured cost

| run | overall | T1 | T2 | T3 | confirmed | probable | inconclusive | invalid | confirmed-wrong |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 8/12 | 5/5 | 1/5 | 2/2 | 8/11 | 0/1 | — | — | 3 |
| 2 | 9/12 | 5/5 | 4/5 | 0/2 | 8/8 | 1/2 | — | 0/2 | 0 |
| 3 | 0/12 | 0/5 | 0/5 | 0/2 | — | — | — | 0/12 | 0 |
| pooled | 17/36 | 10/15 | 5/15 | 2/6 | 16/19 | 1/3 | — | 0/14 | 3 |

## Failed cases

- run 2 t3-overlapping-config-and-oom: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbMLVFLQJkKLgcJNCR'}
- run 2 t3-quiet-selector-loud-crashloop: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbPSjrxLKXSGZ9gouY'}
- run 3 t1-crashloop-missing-env: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbRHrv3kuD6pcM74hL'}
- run 3 t1-imagepull-bogus-tag: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbTGgAPeUDk1S14Ms1'}
- run 3 t1-oom-cache-warmup: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbV6ohpwrJfc9TecNh'}
- run 3 t1-pvc-storageclass-typo: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbX96MMcwETRjnUHhd'}
- run 3 t1-unschedulable-cpu-requests: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbYzDkC1nxESNiZgzE'}
- run 3 t2-init-wait-for-migrations: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbb5EM5dWBZADZHHsk'}
- run 3 t2-quota-blocks-scale: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbcxqc1ysbR3SF7NBd'}
- run 3 t2-rbac-sync-forbidden: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbf17gPVMofqNiJ6wZ'}
- run 3 t2-readiness-wrong-port: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbhdNwTPR4NPyo43o5'}
- run 3 t2-selector-drift-empty-endpoints: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbjvnbMqi7M4CFeenu'}
- run 3 t3-overlapping-config-and-oom: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbmq8n4vB1WxR6BXZA'}
- run 3 t3-quiet-selector-loud-crashloop: BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'You have reached your specified API usage limits. You will regain access on 2026-09-01 at 00:00 UTC.'}, 'request_id': 'req_011CeWSbomDgrzXCrQH2uB7g'}
