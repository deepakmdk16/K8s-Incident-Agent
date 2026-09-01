"""Dump-curation policy for the one-prompt baseline.

This is the documented "what a rushed human would paste" policy (decision doc):
the page text, the full `kubectl get all -A` output, `kubectl describe` of
every not-ready resource, and the last N log lines of every not-ready pod
(both current and previous channel). Nothing else — no events, no manifests,
no targeted queries. The policy is deliberately simple and frozen with the
baseline; baseline/README.md documents it.

All reads are from a recorded fixture directory (see evals/capture.sh layout),
so curation is offline and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LOG_TAIL_LINES = 50

_HEALTHY_POD_STATUSES = frozenset({"Running", "Completed", "Succeeded"})


@dataclass(frozen=True)
class Section:
    """One labeled chunk of pasted output."""

    title: str
    body: str


@dataclass(frozen=True)
class CuratedDump:
    """Everything the baseline prompt contains besides instructions."""

    page: str
    get_all: str
    sections: tuple[Section, ...]

    @property
    def total_chars(self) -> int:
        return (
            len(self.page)
            + len(self.get_all)
            + sum(len(s.title) + len(s.body) for s in self.sections)
        )

    @property
    def approx_tokens(self) -> int:
        """Chars/4 heuristic; the harness records exact API token counts too."""
        return self.total_chars // 4


@dataclass(frozen=True)
class _NotReady:
    namespace: str
    resource: str  # as printed by `get all -A`, e.g. "pod/checkout-worker-abc"


def _parse_ready(field: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)/(\d+)", field)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _line_not_ready(fields: list[str]) -> bool:
    """Not-ready heuristic over one `get all -A` row (fields after namespace).

    Pods: READY a/b with a<b, or a non-healthy STATUS. Workloads printed with a
    READY column (deployments, statefulsets): a<b. Rows printed as
    DESIRED/CURRENT/READY (replicasets, daemonsets): READY < DESIRED. Rows with
    no recognizable readiness shape (services etc.) are never selected.
    """
    resource = fields[0]
    kind = resource.split("/", 1)[0]
    if kind == "pod":
        ready = _parse_ready(fields[1]) if len(fields) > 1 else None
        status = fields[2] if len(fields) > 2 else ""
        return (ready is not None and ready[0] < ready[1]) or (status not in _HEALTHY_POD_STATUSES)
    if len(fields) > 1 and (ready := _parse_ready(fields[1])) is not None:
        return ready[0] < ready[1]
    if len(fields) > 3 and fields[1].isdigit() and fields[3].isdigit():
        return int(fields[3]) < int(fields[1])
    return False


def _not_ready_resources(get_all: str) -> list[_NotReady]:
    found: list[_NotReady] = []
    for line in get_all.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] == "NAMESPACE" or "/" not in fields[1]:
            continue
        if _line_not_ready(fields[1:]):
            found.append(_NotReady(namespace=fields[0], resource=fields[1]))
    return found


def _describe_section(fixture: Path, item: _NotReady) -> Section:
    title = f"kubectl describe {item.resource} -n {item.namespace}"
    path = fixture / "ns" / item.namespace / "describe" / (item.resource.replace("/", "_") + ".txt")
    if not path.is_file():
        return Section(title=title, body="(describe not captured in this snapshot)")
    return Section(title=title, body=path.read_text(encoding="utf-8"))


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-LOG_TAIL_LINES:])


def _log_sections(fixture: Path, item: _NotReady) -> list[Section]:
    pod = item.resource.split("/", 1)[1]
    sections: list[Section] = []
    logs_dir = fixture / "ns" / item.namespace / "logs"
    for path in sorted(logs_dir.glob(f"{pod}__*.log")):
        container, _, channel = path.stem.partition("__")[2].partition(".")
        previous = " --previous" if channel == "previous" else ""
        sections.append(
            Section(
                title=(
                    f"kubectl logs {pod} -c {container} -n {item.namespace}"
                    f"{previous} --tail={LOG_TAIL_LINES}"
                ),
                body=_tail(path.read_text(encoding="utf-8")),
            )
        )
    return sections


def curate(fixture: Path) -> CuratedDump:
    """Apply the documented policy to one fixture directory."""
    page = (fixture / "page.txt").read_text(encoding="utf-8")
    get_all = (fixture / "cluster" / "get-all.txt").read_text(encoding="utf-8")
    sections: list[Section] = []
    for item in _not_ready_resources(get_all):
        sections.append(_describe_section(fixture, item))
        if item.resource.startswith("pod/"):
            sections.extend(_log_sections(fixture, item))
    return CuratedDump(page=page, get_all=get_all, sections=tuple(sections))
