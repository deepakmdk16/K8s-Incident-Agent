"""Offline isomorphic perturbation: a fixture and its gold under a consistent renaming.

A grader that is sensitive to the specific names it was tuned on is a grader
that can be gamed. This module renames every user namespace and every user
object name in a captured snapshot — consistently, across every file and every
path — so the validator can be tested for invariance under renaming
(tests/test_perturb_replay.py) with no cluster and no model call.

Renamed: the user namespaces (`ns/` minus the system set) and every
`metadata.name` in their object lists. A name that embeds another name follows
it — a ReplicaSet `<deploy>-<hash>`, a pod `<rs>-<suffix>`, a claim
`data-<sts>-0`, a decoy one letter off the real name — so the stem is renamed
and the hash, ordinal or typo is kept: pods still belong to their controller,
and a one-letter typo is still a one-letter typo.

Not renamed: kinds, field names, image names, node names, system namespaces,
the capture layout's own vocabulary, the case id, and anything inside those.

A new name is a DNS label of the same length as the old one (column-aligned
describe/get-all text keeps its shape), spelled from consonants only so no
generated name can form a word the validator or a rubric reads as meaning, and
never equal to an alphanumeric run already present in the snapshot. The map is
deterministic for a seed and written to `<out>/rename-map.json`. Nothing is
ever written in place.

    uv run python -m evals.perturb --fixture evals/fixtures/<case> \\
        --gold evals/scenarios/<case>/gold.json --out <dir> --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

SYSTEM_NAMESPACES: frozenset[str] = frozenset(
    {"default", "kube-system", "kube-public", "kube-node-lease", "local-path-storage"}
)

# A DNS-1123 label: the only shape that is renamed (kube-root-ca.crt is a
# system object, and not a label) and the only shape a new name may take.
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")

# A name is matched as whole alphanumeric runs: 'prod' is never found inside
# 'production', but it is the stem of 'prod-eu', 'prod-7d9f8-x2kq4' and
# 'data-prod-0', which is what keeps a derived name attached to the name it
# derives from. Longer names are tried first, so a name that is itself an
# entry is always replaced by its own mapping.
_RUN = re.compile(r"[a-z0-9]+")
_BOUNDARY_LEFT = r"(?<![a-z0-9])"
_BOUNDARY_RIGHT = r"(?![a-z0-9])"

# The consonants of kubectl's own hash alphabet: with no vowel, a generated
# name cannot spell 'fail', 'sure', 'exit' or any other word a rubric or the
# validator reads as meaning.
_CONSONANTS = "bcdfghjklmnpqrstvwxz"
_MAX_DRAWS = 10_000


class PerturbError(ValueError):
    """The perturbation cannot be done as asked; nothing has been written."""


def _with_case_of(found: str, new: str) -> str:
    """`new` in the letter case `found` was written in (payments / Payments / PAYMENTS)."""
    if found == found.lower():
        return new
    if found[0].isupper() and found[1:] == found[1:].lower():
        return new[0].upper() + new[1:]
    if found.isupper():
        return new.upper()
    return new


class Renamer:
    """One rename map, applied to text, to relative paths and to JSON-like values.

    `shields` are tokens kept verbatim even though a renamed name occurs inside
    them (the case id, say): as longer alternatives they win over the name.
    """

    def __init__(self, renames: Mapping[str, str], shields: Iterable[str] = ()) -> None:
        self._renames: dict[str, str] = {old.lower(): new for old, new in renames.items()}
        entries = sorted(set(self._renames) | {s.lower() for s in shields}, key=len, reverse=True)
        if not entries:
            raise PerturbError("a rename map with no entries renames nothing")
        alternatives = "|".join(re.escape(entry) for entry in entries)
        self._pattern = re.compile(
            f"{_BOUNDARY_LEFT}(?:{alternatives}){_BOUNDARY_RIGHT}", re.IGNORECASE
        )

    def _replace(self, match: re.Match[str]) -> str:
        found = match.group(0)
        new = self._renames.get(found.lower())
        return found if new is None else _with_case_of(found, new)

    def text(self, text: str) -> str:
        return self._pattern.sub(self._replace, text)

    def value(self, value: object) -> object:
        """Every string anywhere inside a JSON-like value, renamed; keys are left alone."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in cast(dict[str, object], value).items()}
        if isinstance(value, list):
            return [self.value(v) for v in cast(list[object], value)]
        return value

    def arguments(self, arguments: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], self.value(arguments))


@dataclass(frozen=True)
class RenameMap:
    seed: int
    namespaces: dict[str, str]
    names: dict[str, str]
    kept: tuple[str, ...]
    shields: tuple[str, ...]

    @property
    def renames(self) -> dict[str, str]:
        return {**self.names, **self.namespaces}

    def renamer(self) -> Renamer:
        return Renamer(self.renames, self.shields)

    def as_json(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "namespaces": dict(sorted(self.namespaces.items())),
            "names": dict(sorted(self.names.items())),
            "kept": list(self.kept),
            "shields": list(self.shields),
        }


# --- reading the snapshot -----------------------------------------------------


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PerturbError(f"{path}: expected a JSON object")
    return cast(dict[str, Any], document)


def _item_names(path: Path) -> list[str]:
    """`metadata.name` of every item in one captured object list."""
    document = _load(path)
    if document.get("capture_error"):
        return []
    items = cast(list[dict[str, Any]], document.get("items", []))
    return [str(cast(dict[str, Any], item.get("metadata", {})).get("name", "")) for item in items]


def user_namespaces(fixture: Path) -> list[str]:
    """The captured namespaces that are not Kubernetes' own: the `ns/` directories and
    the names in cluster/namespaces.json, either side counting."""
    captured = {p.name for p in (fixture / "ns").iterdir() if p.is_dir()}
    captured.update(_item_names(fixture / "cluster" / "namespaces.json"))
    return sorted(
        name for name in captured if name not in SYSTEM_NAMESPACES and not name.startswith("kube-")
    )


def _object_names(fixture: Path, namespaces: Sequence[str]) -> set[str]:
    names: set[str] = set()
    for namespace in namespaces:
        for path in sorted((fixture / "ns" / namespace).glob("*.json")):
            names.update(_item_names(path))
    return names


def _layout_runs(fixture: Path, namespaces: Sequence[str]) -> set[str]:
    """Alphanumeric runs of every path component outside the user namespaces.

    These are the capture layout's vocabulary (`ns`, `pods`, `describe`,
    `previous`, …), the system namespaces and every system object's name. A
    user name equal to one of them is left alone rather than renamed into the
    layout: `ns/<ns>/pods.json` has to stay readable.
    """
    user_dirs = {fixture / "ns" / namespace for namespace in namespaces}
    runs: set[str] = set()
    for path in _files(fixture):
        if any(parent in user_dirs for parent in path.parents):
            continue
        runs.update(_RUN.findall(path.relative_to(fixture).as_posix().lower()))
    for node in _item_names(fixture / "cluster" / "nodes.json"):
        runs.update(_RUN.findall(node.lower()))
    # The snapshot's own vocabulary inside the files outside the user
    # namespaces: every JSON key (`webhooks`, `spec`, `data`, …) and every
    # describe label (`Labels:`, `Failure Policy:`). A user name equal to one
    # would otherwise be renamed as text and take the key or label with it, so
    # tools reading the copy would silently find nothing (review of PR #8,
    # 2026-11-07). User-namespace files are excluded on purpose: there a
    # describe's section headers and a ConfigMap's data keys ARE user names
    # (`storefront:` for a container), and must stay renamable.
    for path in _files(fixture):
        if any(parent in user_dirs for parent in path.parents):
            continue
        if path.suffix == ".json":
            runs.update(_json_key_runs(_load(path)))
        elif path.suffix == ".txt" and "describe" in path.relative_to(fixture).as_posix():
            for label in _DESCRIBE_LABEL.findall(path.read_text(encoding="utf-8")):
                runs.update(_RUN.findall(label.lower()))
    return runs


_DESCRIBE_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ./-]*?):(?:\s|$)", re.MULTILINE)


def _json_key_runs(value: object) -> set[str]:
    runs: set[str] = set()
    if isinstance(value, dict):
        for key, inner in cast(dict[str, object], value).items():
            runs.update(_RUN.findall(key.lower()))
            runs.update(_json_key_runs(inner))
    elif isinstance(value, list):
        for inner in cast(list[object], value):
            runs.update(_json_key_runs(inner))
    return runs


def _alphanumeric_runs(fixture: Path, gold: Path) -> set[str]:
    """Every alphanumeric run in the snapshot's files, their relative paths, and the gold."""
    runs: set[str] = set()
    for path in _files(fixture):
        runs.update(_RUN.findall(path.read_text(encoding="utf-8").lower()))
        runs.update(_RUN.findall(path.relative_to(fixture).as_posix().lower()))
    runs.update(_RUN.findall(gold.read_text(encoding="utf-8").lower()))
    return runs


# --- building the map ---------------------------------------------------------


def _fresh(old: str, rng: random.Random, taken: set[str]) -> str:
    """A same-length consonant label with `old`'s dash positions, new to the snapshot."""
    for _ in range(_MAX_DRAWS):
        groups = ["".join(rng.choice(_CONSONANTS) for _ in group) for group in old.split("-")]
        if all(group not in taken for group in groups):
            return "-".join(groups)
    raise PerturbError(f"no fresh name for {old!r} after {_MAX_DRAWS} draws")


def _derived(old: str, assigned: Mapping[str, str]) -> str | None:
    """`old` with the names it embeds already renamed, or None if it embeds none."""
    if not assigned:
        return None
    new = Renamer(assigned).text(old)
    return new if new != old else None


def build_rename_map(fixture: Path, gold: Path, seed: int) -> RenameMap:
    """The deterministic rename map for one fixture and its gold under `seed`."""
    namespaces = user_namespaces(fixture)
    if not namespaces:
        raise PerturbError(f"{fixture}: no user namespace to rename")
    layout = _layout_runs(fixture, namespaces)
    candidates = set(namespaces) | _object_names(fixture, namespaces)
    renamable = {
        name
        for name in candidates
        if DNS_LABEL.fullmatch(name)
        and name not in SYSTEM_NAMESPACES
        and not name.startswith("kube-")
    }
    kept = tuple(sorted(name for name in renamable if name in layout))
    renamable -= set(kept)
    if not renamable:
        raise PerturbError(f"{fixture}: every user name collides with the capture layout")

    taken = _alphanumeric_runs(fixture, gold)
    # Seeded by the seed AND the names: deterministic for a seed, yet two
    # different fixtures do not hand the same new name to their first namespace.
    rng = random.Random(f"{seed}:{' '.join(sorted(renamable))}")
    assigned: dict[str, str] = {}
    # Shortest first, so a name that embeds another is derived from a name that
    # already has its new form.
    for old in sorted(renamable, key=lambda name: (len(name), name)):
        new = _derived(old, assigned) or _fresh(old, rng, taken)
        assigned[old] = new
        taken.update(new.split("-"))

    gold_document = _load(gold)
    anchors = [fixture.name, str(gold_document.get("case_id", ""))] + [
        str(gold_document.get(key, "")) for key in ("tier", "fault_class")
    ]
    unshielded = Renamer(assigned)
    shields = tuple(sorted({a for a in anchors if a and unshielded.text(a) != a}))
    return RenameMap(
        seed=seed,
        namespaces={ns: assigned[ns] for ns in namespaces if ns in assigned},
        names={name: assigned[name] for name in sorted(assigned) if name not in namespaces},
        kept=kept,
        shields=shields,
    )


# --- writing the copy ---------------------------------------------------------


def _check_destination(fixture: Path, gold: Path, out: Path) -> None:
    resolved = out.resolve()
    for protected in (fixture.resolve(), gold.resolve().parent):
        if resolved == protected or protected in resolved.parents:
            raise PerturbError(f"refusing to write inside {protected}: never perturb in place")
    if out.exists() and any(out.iterdir()):
        raise PerturbError(f"{out} exists and is not empty")


def perturb(fixture: Path, gold: Path, out: Path, seed: int) -> RenameMap:
    """Write `<out>/fixture/`, `<out>/gold.json` and `<out>/rename-map.json`."""
    if not (fixture / "ns").is_dir():
        raise PerturbError(f"{fixture}: not a captured fixture (no ns/ directory)")
    if not gold.is_file():
        raise PerturbError(f"{gold}: no such gold file")
    _check_destination(fixture, gold, out)
    rename_map = build_rename_map(fixture, gold, seed)
    renamer = rename_map.renamer()
    for source in _files(fixture):
        relative = source.relative_to(fixture).as_posix()
        target = out / "fixture" / renamer.text(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(renamer.text(source.read_text(encoding="utf-8")), encoding="utf-8")
    (out / "gold.json").write_text(renamer.text(gold.read_text(encoding="utf-8")), encoding="utf-8")
    (out / "rename-map.json").write_text(
        json.dumps(rename_map.as_json(), indent=2) + "\n", encoding="utf-8"
    )
    return rename_map


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--fixture", type=Path, required=True, help="evals/fixtures/<case>")
    parser.add_argument("--gold", type=Path, required=True, help="evals/scenarios/<case>/gold.json")
    parser.add_argument("--out", type=Path, required=True, help="output dir (new or empty)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        rename_map = perturb(args.fixture, args.gold, args.out, args.seed)
    except PerturbError as exc:
        sys.exit(f"perturb: {exc}")
    print(
        f"{args.fixture.name}: renamed {len(rename_map.namespaces)} namespace(s) and "
        f"{len(rename_map.names)} object name(s) with seed {rename_map.seed} -> {args.out}"
        + (f" (kept: {', '.join(rename_map.kept)})" if rename_map.kept else "")
    )


if __name__ == "__main__":
    main()
