"""Price the hedging in a scored run: what each verdict is worth, and where
`inconclusive` lands.

A verdict is the arm's self-reported calibration, so the pooled score alone
cannot show an arm buying its number with hedges. This report prices every
verdict value present: how many rows carry it and their share of all rows
(coverage), and the root-cause-identification rate among them (accuracy). A
row with no verdict goes in an explicit "none" bucket, never dropped.

Beside that, the inconclusive rows are split by whether the solution's read
tools serve the gold failing-resource kind (`solution.tools.serves_kind`): a
hedge on an object the tools could read is a different finding from a hedge
on an object no tool serves. That readability notion is the solution's tool
layer, applied to EVERY arm's rows, so the arms are read on one footing.

Report only — no bar, no threshold. A bar needs a pre-registered threshold
and a second scoring (STATUS 2.3); both `evals/run_eval.py` (summary.json and
summary.md) and `evals/verify_reported.py` (the printed table) only print it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from evals import scoring
from solution import tools

# Bucket label for a row that carries no verdict at all.
NO_VERDICT = "none"
# Known bucket labels in reporting order; any other value follows, sorted.
VERDICT_ORDER: tuple[str, ...] = ("confirmed", "probable", "inconclusive", "invalid", NO_VERDICT)
READABILITY = (
    "readable = solution.tools.serves_kind(gold failing-resource kind): the solution's "
    "tool layer, applied to every arm's rows"
)


@dataclass(frozen=True)
class Row:
    """What the report needs from one scored row, whichever arm produced it."""

    verdict: str | None
    root_cause_correct: bool
    gold_kind: str


def readable_kind(kind: str) -> bool:
    """Whether the solution's read tools serve this kind — False for a kind the
    tool layer does not know, which no tool reads (`serves_kind` answers rather
    than raises for it, so a report-only summary never aborts a finished run)."""
    return tools.serves_kind(kind)


def verdict_order(values: Iterable[str]) -> list[str]:
    """The known labels in VERDICT_ORDER first, then anything else sorted."""
    present = set(values)
    return [v for v in VERDICT_ORDER if v in present] + sorted(present - set(VERDICT_ORDER))


def fraction(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "—"
    return f"{numerator}/{denominator} ({100 * numerator / denominator:.1f}%)"


@dataclass(frozen=True)
class Report:
    """Per-verdict coverage and accuracy, plus the inconclusive readability split."""

    by_verdict: dict[str, scoring.RateCell]
    rows: int
    inconclusive_readable: int
    inconclusive_unreadable: int

    @classmethod
    def from_rows(cls, rows: Iterable[Row]) -> Report:
        cells: dict[str, scoring.RateCell] = {}
        total = readable = unreadable = 0
        for row in rows:
            total += 1
            cell = cells.setdefault(row.verdict or NO_VERDICT, scoring.RateCell())
            cell.cases += 1
            cell.correct += int(row.root_cause_correct)
            if row.verdict == "inconclusive":
                if readable_kind(row.gold_kind):
                    readable += 1
                else:
                    unreadable += 1
        ordered = {v: cells[v] for v in verdict_order(cells)}
        return cls(ordered, total, readable, unreadable)

    def as_dict(self) -> dict[str, object]:
        return {
            "by_verdict": {
                v: {
                    "rows": c.cases,
                    "correct": c.correct,
                    "coverage": round(c.cases / self.rows, 4),
                    "accuracy": round(c.correct / c.cases, 4),
                }
                for v, c in self.by_verdict.items()
            },
            "inconclusive_on_readable_kind": self.inconclusive_readable,
            "inconclusive_on_unreadable_kind": self.inconclusive_unreadable,
            "readability": READABILITY,
        }

    def markdown_lines(self) -> list[str]:
        lines = [
            "## Hedging (pooled; report only, no bar)",
            "",
            "| verdict | coverage | accuracy |",
            "|---|---|---|",
        ]
        lines += [
            f"| {v} | {fraction(c.cases, self.rows)} | {fraction(c.correct, c.cases)} |"
            for v, c in self.by_verdict.items()
        ]
        lines += [
            "",
            f"- inconclusive on a readable kind: {self.inconclusive_readable}; "
            f"on an unreadable kind: {self.inconclusive_unreadable} ({READABILITY})",
        ]
        return lines
