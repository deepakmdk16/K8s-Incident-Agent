"""Additive v3 column: did the submitted FIX name the right field and value?

The frozen scorer (evals/scoring.py; evals/scoring_v2.py for the additive
root) scores the failing resource and the mechanism SENTENCE. The solution
arm's submission also names the fix — `remediation.field_path`,
`remediation.required_value` — and this module scores that against a v3 gold
(evals/gold-v3/<case_id>.json) authored from each case's fault.yaml, notes.md
and remediation_summary. It is a DIAGNOSTIC column reported beside the
frozen root-cause number, never a replacement for it and never a bar: the
frozen metric and case set are untouched, v1's `root_cause_correct` /
`class_correct` are read from rows.jsonl and never recomputed here.

Offline and deterministic: it reads a committed bundle
(evals/results/<stamp>-<arm>/) and the gold tree; no LLM, no network.

Arms whose submission contract does not carry the fix fields (baseline,
rules) report "n/a" — never 0 — because a missing column is not a wrong
answer. A solution row with no accepted submission (salvaged or errored)
scores as a miss of kind `no_submission`: the agent named no fix.

    uv run python -m evals.scoring_v3 evals/results/<bundle> [--json PATH]
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast

GOLD_DIR = Path(__file__).resolve().parent / "gold-v3"
TIERS: tuple[str, ...] = ("T1", "T2", "T3")

# Only this arm's submit_answer contract carries remediation.field_path /
# required_value (solution/tools.py). Extending the shared contract to another
# arm is a separate, disclosed decision; until then every other arm is n/a.
ARMS_EMITTING_FIX_FIELDS: frozenset[str] = frozenset({"solution"})

# A fix that deletes the object names no field. These spellings (after the
# normalization below, so whitespace and case are gone) all canonicalize to
# the whole-object path the gold uses.
OBJECT_PATH = "<object>"
_OBJECT_SPELLINGS: frozenset[str] = frozenset(
    {"", "object", "wholeobject", "entireobject", "theobject"}
)

STATUS_SCORED = "scored"
STATUS_NO_SUBMISSION = "no_accepted_submission"
STATUS_NOT_EMITTED = "fields_not_emitted"


# --- normalization -------------------------------------------------------------


def normalize_field_path(raw: str) -> str:
    """Canonical dotted form with [i] indexes; see evals/gold-v3/README.md.

    Rules, in order: strip whitespace and backticks; lowercase; JSON-pointer
    slashes become dots; a leading `$`/`.` is dropped; quotes vanish
    (`[name="api"]` -> `[name=api]`, `["app"]` -> `[app]`); a dotted numeric
    segment becomes an index (`containers.0.image` -> `containers[0].image`);
    `[name:api]` becomes `[name=api]`; `.[` becomes `[`; the whole-object
    spellings become `<object>`. Nothing else — the gold lists the name-form
    aliases explicitly, so no fixture lookup happens here.
    """
    text = re.sub(r"\s+", "", raw.replace("`", "")).lower()
    text = text.replace("/", ".").lstrip("$.")
    text = re.sub(r"""['"]""", "", text)
    text = re.sub(r"\.(\d+)(?=\.|$)", r"[\1]", text)
    text = re.sub(r"\[(\w+):", r"[\1=", text)
    text = text.replace(".[", "[")
    return OBJECT_PATH if text.strip("()<>") in _OBJECT_SPELLINGS else text


def normalize_value(raw: str) -> str:
    """Case-insensitive, quote/brace/whitespace-free, `=` folded into `:`.

    `{"app": "catalog"}`, `app=catalog` and `app: catalog` all become
    `app:catalog`; `"8080"` becomes `8080`.
    """
    text = re.sub(r"""[`'"{}\s]""", "", raw).lower()
    return text.replace("=", ":")


# --- gold ----------------------------------------------------------------------


@dataclass(frozen=True)
class GoldFix:
    """One evals/gold-v3/<case_id>.json; schema in evals/gold-v3/README.md."""

    case_id: str
    field_path: str
    accepted_field_paths: tuple[str, ...]
    required_value: str | None
    accepted_required_values: tuple[str, ...]
    source: str

    def path_forms(self) -> frozenset[str]:
        paths = (self.field_path, *self.accepted_field_paths)
        return frozenset(normalize_field_path(p) for p in paths)

    def value_forms(self) -> frozenset[str] | None:
        """None when the scenario pins no single correct value (value is n/a)."""
        if self.required_value is None:
            return None
        return frozenset(
            normalize_value(v) for v in (self.required_value, *self.accepted_required_values)
        )


def _str_list(obj: dict[str, object], key: str, errors: list[str]) -> tuple[str, ...]:
    value = obj.get(key)
    items = cast(list[object], value) if isinstance(value, list) else None
    if items is None or not all(isinstance(v, str) for v in items):
        errors.append(f"{key} must be a list of strings")
        return ()
    return tuple(cast(list[str], items))


def load_gold(path: Path) -> GoldFix:
    """Load and validate one gold-v3 file. Raises ValueError; never defaults."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: gold JSON must be an object")
    obj = cast(dict[str, object], data)
    errors: list[str] = []
    case_id = obj.get("case_id")
    if not isinstance(case_id, str) or case_id != path.stem:
        errors.append(f"case_id must equal the file stem {path.stem!r}, got {case_id!r}")
        case_id = path.stem
    field_path = obj.get("field_path")
    if not isinstance(field_path, str) or not field_path.strip():
        errors.append("field_path must be a non-empty string")
        field_path = ""
    accepted_paths = _str_list(obj, "accepted_field_paths", errors)
    required_value = obj.get("required_value")
    if required_value is not None and (
        not isinstance(required_value, str) or not required_value.strip()
    ):
        errors.append("required_value must be a non-empty string or null")
        required_value = None
    accepted_values = _str_list(obj, "accepted_required_values", errors)
    if required_value is None and accepted_values:
        errors.append("accepted_required_values must be empty when required_value is null")
    source = obj.get("source")
    if not isinstance(source, str) or not source.strip():
        errors.append("source must name the fault.yaml/notes.md/gold lines it was authored from")
        source = ""
    if errors:
        raise ValueError(f"{path}: invalid gold-v3: " + "; ".join(errors))
    return GoldFix(
        case_id=case_id,
        field_path=field_path,
        accepted_field_paths=accepted_paths,
        required_value=required_value,
        accepted_required_values=accepted_values,
        source=source,
    )


def load_gold_dir(gold_dir: Path = GOLD_DIR) -> dict[str, GoldFix]:
    golds = [load_gold(p) for p in sorted(gold_dir.glob("*.json"))]
    if not golds:
        raise ValueError(f"no gold-v3 files under {gold_dir}")
    return {g.case_id: g for g in golds}


# --- rows ----------------------------------------------------------------------


@dataclass(frozen=True)
class FixRow:
    """One scored bundle row: the v1 verdicts carried over, the v3 column beside."""

    run: int
    case_id: str
    tier: str
    root_cause_correct: bool
    class_correct: bool
    status: str
    field_path: str | None
    required_value: str | None
    fix_path_correct: bool | None
    fix_value_correct: bool | None
    fix_both_correct: bool | None
    miss: str | None

    @property
    def value_open(self) -> bool:
        return self.status == STATUS_SCORED and self.fix_value_correct is None


@dataclass
class TierTotals:
    """Count pairs behind every reported cell; rates are never stored."""

    rows: int = 0
    root_cause_correct: int = 0
    path_correct: int = 0
    value_scorable: int = 0
    value_correct: int = 0
    both_correct: int = 0
    no_submission: int = 0
    value_open: int = 0

    def add(self, row: FixRow) -> None:
        self.rows += 1
        self.root_cause_correct += int(row.root_cause_correct)
        self.path_correct += int(bool(row.fix_path_correct))
        self.no_submission += int(row.status == STATUS_NO_SUBMISSION)
        self.value_open += int(row.value_open)
        if row.fix_value_correct is not None:
            self.value_scorable += 1
            self.value_correct += int(row.fix_value_correct)
            self.both_correct += int(bool(row.fix_both_correct))


@dataclass
class BundleResult:
    bundle: str
    arm: str
    emits_fix_fields: bool
    rows: list[FixRow] = field(default_factory=list[FixRow])
    by_tier: dict[str, TierTotals] = field(default_factory=dict[str, TierTotals])
    overall: TierTotals = field(default_factory=TierTotals)

    def misses(self) -> list[FixRow]:
        return [r for r in self.rows if r.miss is not None]


def _miss_kind(path_ok: bool, value_ok: bool | None) -> str | None:
    if path_ok and value_ok is not False:
        return None
    if not path_ok and value_ok is False:
        return "path+value"
    return "value" if path_ok else "path"


def score_submission(
    row: dict[str, object], remediation: dict[str, object] | None, gold: GoldFix
) -> FixRow:
    """Score one accepted remediation node (or its absence) against the gold."""
    value_forms = gold.value_forms()
    field_path: str | None = None
    required_value: str | None = None
    if remediation is None:
        status, path_ok, miss = STATUS_NO_SUBMISSION, False, "no_submission"
        value_ok = None if value_forms is None else False
    else:
        status = STATUS_SCORED
        field_path = str(remediation.get("field_path", ""))
        required_value = str(remediation.get("required_value", ""))
        path_ok = normalize_field_path(field_path) in gold.path_forms()
        value_ok = None if value_forms is None else normalize_value(required_value) in value_forms
        miss = _miss_kind(path_ok, value_ok)
    return FixRow(
        run=int(cast(int, row["run"])),
        case_id=str(row["case_id"]),
        tier=str(row["tier"]),
        root_cause_correct=bool(row["root_cause_correct"]),
        class_correct=bool(row["class_correct"]),
        status=status,
        field_path=field_path,
        required_value=required_value,
        fix_path_correct=path_ok,
        fix_value_correct=value_ok,
        fix_both_correct=None if value_ok is None else (path_ok and value_ok),
        miss=miss,
    )


def _not_emitted(row: dict[str, object]) -> FixRow:
    return FixRow(
        run=int(cast(int, row["run"])),
        case_id=str(row["case_id"]),
        tier=str(row["tier"]),
        root_cause_correct=bool(row["root_cause_correct"]),
        class_correct=bool(row["class_correct"]),
        status=STATUS_NOT_EMITTED,
        field_path=None,
        required_value=None,
        fix_path_correct=None,
        fix_value_correct=None,
        fix_both_correct=None,
        miss=None,
    )


def accepted_remediation(bundle: Path, row: dict[str, object]) -> dict[str, object] | None:
    """The `remediation` node of the row's accepted submit_answer call, or None.

    solution/agent.py breaks out of its loop on the first accepted submit, so
    the LAST submit_answer in the transcript is the accepted one — unless the
    row was salvaged (`metrics.salvaged`), in which case nothing was accepted
    and the report's fix fields are the salvage's "(not established)".
    """
    metrics = cast(dict[str, object], row.get("metrics") or {})
    if row.get("error") is not None or bool(metrics.get("salvaged")):
        return None
    transcript = bundle / f"run{row['run']}" / str(row["case_id"]) / "transcript.jsonl"
    if not transcript.is_file():
        return None
    last: dict[str, object] | None = None
    for line in transcript.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = cast(dict[str, object], json.loads(line))
        for call in cast(list[dict[str, object]], record.get("calls", [])):
            if call.get("name") == "submit_answer":
                arguments = cast(dict[str, object], call.get("arguments", {}))
                node = arguments.get("remediation")
                last = cast(dict[str, object], node) if isinstance(node, dict) else None
    return last


def _read_rows(bundle: Path) -> list[dict[str, object]]:
    rows_path = bundle / "rows.jsonl"
    if not rows_path.is_file():
        raise FileNotFoundError(f"{bundle}: no rows.jsonl — not a scored bundle")
    return [
        cast(dict[str, object], json.loads(line))
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _bundle_arm(bundle: Path) -> str:
    summary_path = bundle / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"{bundle}: no summary.json — cannot tell which arm ran")
    summary = cast(dict[str, object], json.loads(summary_path.read_text(encoding="utf-8")))
    arm = summary.get("arm")
    if not isinstance(arm, str) or not arm:
        raise ValueError(f"{summary_path}: 'arm' missing")
    return arm


def score_bundle(bundle: Path, gold_dir: Path = GOLD_DIR) -> BundleResult:
    """Score every row of one committed bundle; raises on a case with no gold-v3."""
    arm = _bundle_arm(bundle)
    emits = arm in ARMS_EMITTING_FIX_FIELDS
    golds = load_gold_dir(gold_dir)
    result = BundleResult(bundle=bundle.name, arm=arm, emits_fix_fields=emits)
    for row in _read_rows(bundle):
        if not emits:
            scored = _not_emitted(row)
        else:
            case_id = str(row["case_id"])
            if case_id not in golds:
                raise ValueError(f"{bundle.name}: no gold-v3 for case {case_id!r} in {gold_dir}")
            scored = score_submission(row, accepted_remediation(bundle, row), golds[case_id])
        result.rows.append(scored)
        result.by_tier.setdefault(scored.tier, TierTotals()).add(scored)
        result.overall.add(scored)
    return result


# --- output --------------------------------------------------------------------


def _cell(correct: int, cases: int, emits: bool) -> str:
    return f"{correct}/{cases}" if emits else "n/a"


def _table_line(label: str, t: TierTotals, emits: bool) -> str:
    return (
        f"| {label} | {t.rows} | {t.root_cause_correct}/{t.rows} "
        f"| {_cell(t.path_correct, t.rows, emits)} "
        f"| {_cell(t.value_correct, t.value_scorable, emits)} "
        f"| {_cell(t.both_correct, t.value_scorable, emits)} "
        f"| {t.no_submission if emits else 'n/a'} | {t.value_open if emits else 'n/a'} |"
    )


def render_markdown(result: BundleResult) -> str:
    """The stdout table: v1 root-cause beside the v3 fix columns, per tier."""
    lines = [
        f"## scoring_v3 — {result.bundle} (arm: {result.arm})",
        "",
        "| tier | rows | root_cause_correct (v1) | fix_path_correct | fix_value_correct "
        "| both_correct | no accepted submission | value open in gold |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for tier in TIERS:
        if tier in result.by_tier:
            lines.append(_table_line(tier, result.by_tier[tier], result.emits_fix_fields))
    lines.append(_table_line("all", result.overall, result.emits_fix_fields))
    if not result.emits_fix_fields:
        lines.append("")
        lines.append(
            f"arm '{result.arm}' does not emit remediation.field_path / required_value; "
            "the v3 column is n/a (not 0) for every row."
        )
        return "\n".join(lines) + "\n"
    lines.append("")
    lines.append(
        "fix_value_correct / both_correct are over rows whose gold pins a single value; "
        "'value open in gold' rows score the path only. Diagnostic column, not a bar."
    )
    misses = result.misses()
    lines.append("")
    lines.append(f"### misses ({len(misses)})")
    for r in misses:
        lines.append(
            f"- run{r.run} {r.case_id} [{r.tier}] {r.miss}: "
            f"field_path={r.field_path!r} required_value={r.required_value!r}"
        )
    return "\n".join(lines) + "\n"


def to_json(result: BundleResult) -> dict[str, object]:
    return {
        "bundle": result.bundle,
        "arm": result.arm,
        "emits_fix_fields": result.emits_fix_fields,
        "overall": asdict(result.overall),
        "by_tier": {tier: asdict(t) for tier, t in sorted(result.by_tier.items())},
        "rows": [asdict(r) for r in result.rows],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Score a bundle's submitted fixes (field_path / required_value) "
        "against evals/gold-v3; additive diagnostic column beside the frozen scorer."
    )
    parser.add_argument("bundle", type=Path, help="evals/results/<stamp>-<arm> directory")
    parser.add_argument("--json", type=Path, default=None, help="also write the result as JSON")
    parser.add_argument("--gold-dir", type=Path, default=GOLD_DIR)
    args = parser.parse_args(argv)
    result = score_bundle(args.bundle, args.gold_dir)
    print(render_markdown(result), end="")
    if args.json is not None:
        args.json.write_text(json.dumps(to_json(result), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
