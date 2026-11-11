# gold-v3 — the fix, not the sentence

An **additive** gold tree for `evals/scoring_v3.py`. One file per case,
`<case_id>.json`, for the 12 frozen cases (`evals/scenarios/`), the 3
additive cases (`evals/scenarios-v2/`) and the 5 held-out cases
(`evals/scenarios-heldout/`, authored 2026-11-11 before any LLM arm read that
set; `tests/test_scoring_v3.py` requires one file for every case in every root
`evals/run_eval.py` scores). The frozen `gold.json` files are
untouched; this tree adds the one thing they lack — the field the fix changes
and the value it changes it to — so the solution arm's submitted
`remediation.field_path` / `required_value` can be scored mechanically.

This is a diagnostic column reported beside the frozen root-cause number
(`root_cause_correct`, read from `rows.jsonl`, never recomputed). It is not
the primary metric and not an acceptance bar; see
`docs/experiments/2026-11-07-scoring-v3.md`.

## Schema

```json
{
  "case_id": "t2-readiness-wrong-port",
  "field_path": "spec.template.spec.containers[0].readinessProbe.httpGet.port",
  "accepted_field_paths": ["spec.template.spec.containers[name=api].readinessProbe.httpGet.port"],
  "required_value": "8080",
  "accepted_required_values": ["8080", "http"],
  "source": "fault.yaml L46-55 ...; gold.json remediation_summary ...; notes.md L27 ..."
}
```

- `field_path` — the canonical path, dotted, list elements by index `[i]`,
  relative to the failing resource's object root (`spec.…`, `subjects[0].…`,
  `webhooks[0].…`). A fix that deletes the whole object has the path
  `<object>`.
- `accepted_field_paths` — equivalent spellings: the name form of every
  indexed element on the path (`containers[name=api]`, `env[name=DB_HOST]`),
  the whole-map form where the fix is one key of a small map
  (`spec.selector` for `spec.selector.app`), and `<object>` where deletion is
  a documented alternative. The primary path is implicitly accepted.
- `required_value` — the single correct value, or `null` when the scenario
  pins none (see "Open values"). `accepted_required_values` — equivalent
  spellings (a named port for its number, a Service's DNS forms, the RBAC
  subject spelling of a ServiceAccount, the whole-selector map for a label
  value). Must be empty when `required_value` is `null`.
- `source` — which lines of the case's `fault.yaml`, `notes.md` and the
  frozen `gold.json`'s `remediation_summary` the entry was authored from, and
  why the value is pinned or open. Every entry is traceable to the scenario.

## Authoring rule

1. The path is read off `fault.yaml`: the object the frozen gold names as the
   failing resource, the list element and key that carry the wrong value.
2. A name-form alias is listed **only when the fixture makes it
   unambiguous** — exactly one element with that name in that list (every
   pod template in the case set has one container, one init container, one
   env entry). The mapping is done here, by the author, not by the scorer:
   the scorer does no fixture lookup, so the rule stays one line.
3. The value is **pinned** only when the fixture admits exactly one correct
   value: a typo'd name, a wrong port, a wrong key, a wrong label, a wrong
   subject, a policy enum. Sizes, replica counts, image references and URLs
   are **open** (`null`): gold's example (`1Gi`, `250m`, `8`) is one choice
   among many, and exact-matching it would score a correct fix wrong.
4. Deletion of the object (`<object>`) is accepted only where the frozen
   remediation_summary or notes.md names it as a fix.
5. Nothing here is authored from an arm's output. The tree was written from
   the scenarios before the scorer was run over any bundle and is frozen at
   that commit; a spelling the rule should obviously have accepted is
   reported as a miss and logged as a candidate rule change, never applied
   after the fact.

## Matching (`evals/scoring_v3.py`)

Field paths are normalized before comparison: whitespace and backticks
stripped, lowercased, JSON-pointer slashes to dots, a leading `$`/`.`
dropped, quotes removed (`[name="api"]` → `[name=api]`, `["app"]` → `[app]`),
dotted numeric segments to indexes (`containers.0.image` →
`containers[0].image`), `[name:api]` → `[name=api]`, `.[` → `[`, and the
whole-object spellings (`<object>`, `(object)`, `object`, `whole object`,
`entire object`, `the object`, empty) → `<object>`. Then exact match against
the normalized accepted set — since v3.1 (2026-11-07) through every *reading*
of the path: a bracket that is neither a numeric index nor `key=value`
(`containers[api]`, `spec.hard[pods]`) is read both as `[name=api]` and as
`.api`, and the path matches if any reading is in the set (`path_readings`;
decision and evidence in `docs/experiments/2026-11-07-scoring-v3.md`, "v3.1").
No JSONPath filters, no schema knowledge.

Values are normalized by lowercasing and removing quotes, backticks, braces
and whitespace, with `=` folded into `:` — so `{"app": "catalog"}`,
`app=catalog` and `app: catalog` all become `app:catalog`, and `"8080"`
becomes `8080`. Then exact match against the normalized accepted set. A value
wrapped in prose (`8080 (the containerPort)`) does not match and is reported
as a value miss with the raw string beside it.

Known limitation: the accepted sets are pooled per case, not paired. For a
case with two accepted fixes (`t2-checkout-release-stalled`: delete the
object, or set `failurePolicy: Ignore`), a submission that pairs one fix's
path with the other's value still scores both-correct. The per-row output
carries the raw strings so a reader can see it.

## Open values

`required_value: null` in: `t1-crashloop-missing-env` (any non-empty
AMQP_URL), `t1-imagepull-bogus-tag` (any existing image reference),
`t1-oom-cache-warmup` (any limit with headroom), `t1-reefer-telemetry-offline`
and `t1-unschedulable-cpu-requests` (any schedulable request),
`t2-quota-blocks-scale` (any cap ≥ 6, or deletion). For these rows the scorer reports `fix_value_correct` and
`both_correct` as not scorable (they are excluded from those denominators and
counted under "value open in gold"); the path still scores.
