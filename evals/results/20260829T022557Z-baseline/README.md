# DISCLOSED-PARTIAL RUN — not the headline number

This bundle is committed as raw evidence but is **not a valid scored
baseline**: the API key's credit balance was exhausted mid-run, so 14 of 36
case-runs failed with a billing 400 ("credit balance is too low"), which the
harness — correctly — records as failed rows scored wrong. The summary
numbers therefore mix model outcomes with infrastructure failures and must
not be quoted.

Clean subset (verifiable in rows.jsonl via the `error` field): run 1
complete (12/12 cases), run 2 partial (10/12); 17/22 clean rows
root-cause-correct. Failed rows all carry the billing error string; none
reached the model.

Disposition: re-run the full 12 x 3 after credits are restored; that bundle
becomes the baseline evidence. Failure mode logged in
docs/failure-modes.md (2026-08-29).
